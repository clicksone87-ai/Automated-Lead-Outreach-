"""Gmail outreach (Track A) with fake IMAP/SMTP servers, compose links, "Mark as sent", and
ZeroBounce with the local fallback. No live calls."""

from __future__ import annotations

import email
import uuid
from email import policy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import dns.resolver
import httpx
import pytest
import respx
from sqlalchemy import select

from app.adapters.email import EmailAttachment, OutgoingEmail
from app.adapters.email_free import LocalVerifier
from app.adapters.gmail import (
    COMPOSE_URL_LIMIT,
    GmailComposeLink,
    GmailEmail,
    compose_url,
    draft_url,
)
from app.adapters.registry import build_adapters
from app.adapters.verification import FallbackVerifier, ZeroBounceVerifier
from app.config import Settings
from app.db.models import (
    Business,
    Cost,
    DeliveryStatus,
    Lead,
    LeadStatus,
    Message,
    PolicyDecision,
    VerificationStatus,
)
from app.db.session import session_scope
from app.orchestrator import quota
from app.services.sending import MarkSentError, mark_sent
from tests.integration.test_outreach import _approve, _lead, _propose, _send
from tests.integration.test_outreach import _settings as _outreach_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SENDER = "agency.sender@gmail.com"


class FakeImap:
    """Records what the adapter does; LIST reports a localised Drafts folder."""

    instances: list[FakeImap] = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.appended: list[tuple[str, str, bytes]] = []
        FakeImap.instances.append(self)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.calls.append(f"login:{user}")
        return "OK", [b"logged in"]

    def list(self) -> tuple[str, list[bytes]]:
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Drafts) "/" "[Google Mail]/Drafts"',
        ]

    def append(self, folder: str, flags: str, date: Any, raw: bytes) -> tuple[str, list[bytes]]:
        self.appended.append((folder, flags, raw))
        return "OK", [b"[APPENDUID 1 7] (Success)"]

    def logout(self) -> None:
        self.calls.append("logout")


class FakeSmtp:
    instances: list[FakeSmtp] = []

    def __init__(self, host: str, port: int) -> None:
        self.calls: list[str] = []
        self.sent: list[Any] = []
        FakeSmtp.instances.append(self)

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, msg: Any) -> None:
        self.sent.append(msg)

    def quit(self) -> None:
        self.calls.append("quit")


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    FakeImap.instances.clear()
    FakeSmtp.instances.clear()


def _gmail(delivery: str = "draft") -> GmailEmail:
    return GmailEmail(SENDER, "app-pass", delivery=delivery, imap_factory=FakeImap,
                      smtp_factory=FakeSmtp)  # fmt: skip


def _email(**kw: Any) -> OutgoingEmail:
    base: dict[str, Any] = {
        "from_address": f"Test Agency <{SENDER}>",
        "to": "owner@hubcity.example",
        "subject": "A new website for Hub City Plumbing",
        "html": "<p>Hello</p>",
        "text": "Hello",
        "idempotency_key": "p1:0",
        "attachments": [EmailAttachment(filename="logo.png", content=b"\x89PNG", content_type="image/png")],
    }  # fmt: skip
    return OutgoingEmail(**{**base, **kw})


def test_settings_pick_the_gmail_route() -> None:
    def s(**kw: Any) -> Settings:
        return Settings(_env_file=None, **kw)  # type: ignore[call-arg]

    assert s(gmail_sender="").gmail_mode == ""
    assert s(outreach_email_mode="provider", gmail_sender=SENDER).gmail_mode == ""
    assert s(gmail_sender=SENDER).gmail_mode == "compose"
    assert s(gmail_sender=SENDER, gmail_app_password="x").gmail_mode == "draft"
    sending = s(gmail_sender=SENDER, gmail_app_password="x", gmail_delivery="send")
    assert sending.gmail_mode == "send"
    named = s(gmail_sender=SENDER, sender_business_name="Test Agency", reply_to_email="r@x.com")
    assert named.from_address == f"Test Agency <{SENDER}>"
    assert named.reply_address == "r@x.com"
    assert s(gmail_sender=SENDER).reply_address == SENDER
    assert isinstance(build_adapters(s(gmail_sender=SENDER)).email, GmailComposeLink)
    draft = build_adapters(s(gmail_sender=SENDER, gmail_app_password="x")).email
    assert isinstance(draft, GmailEmail) and draft.hands_off


def test_compose_url_is_prefilled_and_fits_a_link() -> None:
    url = compose_url(SENDER, "a@b.com", "Hi there", "Line 1\nLine 2 & more")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://mail.google.com/mail/?")
    assert q["view"] == ["cm"] and q["to"] == ["a@b.com"] and q["su"] == ["Hi there"]
    assert q["body"] == ["Line 1\nLine 2 & more"] and q["authuser"] == [SENDER]
    long = compose_url(SENDER, "a@b.com", "Hi", "word " * 2000)
    assert len(long) <= COMPOSE_URL_LIMIT
    assert parse_qs(urlparse(long).query)["body"][0].endswith(
        "[…the full message is in the dashboard]"
    )
    link = draft_url(SENDER, "<abc@gmail.com>")
    assert "authuser=agency.sender%40gmail.com" in link
    assert link.endswith("#search/in%3Adrafts%20rfc822msgid%3Aabc%40gmail.com")


async def test_app_password_draft_goes_to_the_drafts_folder_with_attachments() -> None:
    mid = await _gmail().send(_email())
    imap = FakeImap.instances[0]
    assert (imap.host, imap.port) == ("imap.gmail.com", 993)
    assert imap.calls == [f"login:{SENDER}", "logout"]
    folder, flags, raw = imap.appended[0]
    assert folder == '"[Google Mail]/Drafts"' and flags == "(\\Draft)"
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert msg["Message-ID"] == mid and mid.endswith("@gmail.com>")
    assert msg["From"] == f"Test Agency <{SENDER}>" and msg["To"] == "owner@hubcity.example"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["logo.png"]
    assert FakeSmtp.instances == []  # nothing was sent


async def test_app_password_send_uses_starttls_smtp() -> None:
    await _gmail("send").send(_email())
    smtp = FakeSmtp.instances[0]
    assert smtp.calls == ["starttls", f"login:{SENDER}", "quit"]
    assert smtp.sent[0]["Subject"] == "A new website for Hub City Plumbing"
    assert FakeImap.instances == []


def _gmail_settings(tmp: Path) -> Settings:
    base = _outreach_settings(tmp)
    return base.model_copy(update={"gmail_sender": SENDER})


async def _drafted_lead(tmp: Path, adapter: Any) -> tuple[uuid.UUID, Any]:
    settings = _gmail_settings(tmp)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    outcome = await _send(settings, lead_id, adapter)
    return lead_id, outcome


async def test_outreach_puts_a_draft_in_gmail_and_waits_for_mark_as_sent(tmp_path: Path) -> None:
    lead_id, outcome = await _drafted_lead(tmp_path, _gmail())
    assert outcome.status == LeadStatus.AWAITING_SEND
    assert "Gmail Drafts" in outcome.reason
    _, _, raw = FakeImap.instances[0].appended[0]
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert len(list(msg.iter_attachments())) >= 2  # logo, preview, audit travel with the draft
    async with session_scope() as s:
        m = (await s.execute(select(Message))).scalar_one()
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        biz = await s.get(Business, lead.business_id)
        gmail_units = (
            (await s.execute(select(Cost.units).where(Cost.provider == "gmail"))).scalars().all()
        )
    assert m.delivery_status == DeliveryStatus.DRAFTED and m.sent_at is None
    assert m.body_text and m.message_id_header == m.provider_id
    assert lead.status == LeadStatus.AWAITING_SEND
    assert biz is not None and biz.cooldown_until is None  # not contacted yet
    assert gmail_units == [1.0]

    # Running outreach again does not create a second draft.
    settings = _gmail_settings(tmp_path)
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        lead.status = LeadStatus.APPROVED
    await _send(settings, lead_id, _gmail())
    assert len(FakeImap.instances) == 1

    async with session_scope() as s:
        sent = await mark_sent(s, m.id, by="owner")
        assert sent.delivery_status == DeliveryStatus.SENT and sent.sent_at is not None
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None and lead.status == LeadStatus.SENT
        biz = await s.get(Business, lead.business_id)
        assert biz is not None and biz.cooldown_until is not None
        with pytest.raises(MarkSentError):
            await mark_sent(s, m.id, by="owner")


async def test_gmail_daily_cap_stops_new_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capped = Settings(_env_file=None, gmail_daily_send_cap=0)  # type: ignore[call-arg]
    monkeypatch.setattr(quota, "get_settings", lambda: capped)
    _, outcome = await _drafted_lead(tmp_path, _gmail())
    assert outcome.status != LeadStatus.AWAITING_SEND
    assert FakeImap.instances == []


async def test_outbox_api_compose_link_and_mark_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.registry import get_adapters  # noqa: PLC0415
    from app.config import get_settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    lead_id, outcome = await _drafted_lead(tmp_path, GmailComposeLink(SENDER))
    assert outcome.status == LeadStatus.AWAITING_SEND and "compose link" in outcome.reason
    settings = _gmail_settings(tmp_path)
    adapters = build_adapters(settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_adapters] = lambda: adapters
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        box = (await client.get("/api/gmail/outbox")).json()
        assert box["mode"] == "compose" and box["sender"] == SENDER
        item = box["items"][0]
        assert item["lead_id"] == str(lead_id) and item["kind"] == "compose"
        q = parse_qs(urlparse(item["open_url"]).query)
        assert q["to"] == ["owner@hubcity.example"] and q["su"][0] == item["subject"]
        assert item["attachments"] and all(
            a["url"].startswith("/api/files/") for a in item["attachments"]
        )
        done = await client.post(
            f"/api/messages/{item['message_id']}/mark-sent", json={"by": "owner"}
        )
        assert done.status_code == 200 and done.json()["delivery_status"] == "sent"
        again = await client.post(
            f"/api/messages/{item['message_id']}/mark-sent", json={"by": "owner"}
        )
        assert again.status_code == 409
        missing = await client.post(f"/api/messages/{uuid.uuid4()}/mark-sent", json={})
        assert missing.status_code == 404
        assert (await client.get("/api/gmail/outbox")).json()["items"] == []


class _Resolver:
    async def resolve(self, domain: str, rtype: str, lifetime: float = 0) -> list[Any]:
        if domain == "nowhere.invalid":
            raise dns.resolver.NXDOMAIN
        return [type("MX", (), {"preference": 10, "exchange": "mx.example.com."})()]


@respx.mock
async def test_zerobounce_counts_free_quota_then_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capped = Settings(_env_file=None, zerobounce_monthly_cap=1)  # type: ignore[call-arg]
    monkeypatch.setattr(quota, "get_settings", lambda: capped)
    route = respx.get(ZeroBounceVerifier.ENDPOINT).mock(
        return_value=httpx.Response(200, json={"status": "valid", "sub_status": ""})
    )
    verifier = FallbackVerifier(ZeroBounceVerifier("zk"), LocalVerifier(resolver=_Resolver()))
    async with session_scope() as s:
        first = await verifier.verify(s, "owner@acme.com", agent="contact_enrichment")
        second = await verifier.verify(s, "owner@acme.com", agent="contact_enrichment")
        bad = await verifier.verify(s, "x@nowhere.invalid", agent="contact_enrichment")
    assert first.provider_status == "valid" and route.call_count == 1
    assert second.provider_status == "syntax_mx_ok"  # cap reached: local check took over
    assert second.status == VerificationStatus.VALID
    assert bad.status == VerificationStatus.INVALID
    async with session_scope() as s:
        used = (await s.execute(select(Cost).where(Cost.provider == "zerobounce"))).scalars().all()
        errs = (
            (
                await s.execute(
                    select(PolicyDecision).where(PolicyDecision.check == "provider_error")
                )
            )
            .scalars()
            .all()
        )
    assert len(used) == 1 and used[0].amount_usd == 0 and used[0].free_tier
    assert errs and errs[0].action == "zerobounce"


@respx.mock
async def test_zerobounce_error_falls_back_and_credits_read() -> None:
    respx.get(ZeroBounceVerifier.ENDPOINT).mock(return_value=httpx.Response(500))
    respx.get(ZeroBounceVerifier.CREDITS).mock(
        return_value=httpx.Response(200, json={"Credits": "97"})
    )
    zb = ZeroBounceVerifier("zk")
    assert await zb.credits() == 97
    async with session_scope() as s:
        r = await FallbackVerifier(zb, LocalVerifier(resolver=_Resolver())).verify(
            s, "owner@acme.com", agent="t"
        )
    assert r.provider_status == "syntax_mx_ok"
