"""Proposal and outreach flow: evidence-mapped claims, the sandbox-aware gate, sends, unsubscribe
links, replies, follow-ups and autonomy gating. No live calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.email import DeliveryEvent, OutgoingEmail
from app.adapters.mailbox import MailItem
from app.adapters.registry import build_adapters
from app.agents.outreach.agent import OutreachAgent
from app.agents.proposal.agent import Claim, ProposalAgent, ProposalDraft
from app.config import Settings
from app.db.models import (
    Audit,
    Brief,
    Business,
    Contact,
    Deal,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
    ManualOutreach,
    Message,
    Preview,
    Proposal,
    Reply,
    Suppression,
)
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext
from app.services import autonomy, outreach_jobs, runtime_settings
from app.services.sending import is_business_time, next_send_time
from app.services.unsubscribe import make_token, read_token

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(tmp: Path) -> Settings:
    return Settings(_env_file=None, demo_mode=True, data_dir=tmp, sender_name="Sam", sender_business_name="Test Agency",
                    sender_postal_address="1 Test Street", portfolio_url="https://example.com")  # type: ignore[call-arg]  # fmt: skip


class SandboxEmail:
    """Behaves like SMTP → Mailpit: records messages, reports delivered."""

    name: ClassVar[str] = "sandbox_email"
    is_mock: ClassVar[bool] = False
    is_sandbox = True

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> str:
        self.sent.append(email)
        return f"<{uuid.uuid4().hex}@test-agency.local>"

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="delivered")


class RealEmail(SandboxEmail):
    is_sandbox = False


async def _lead(settings: Settings, *, email: str = "owner@hubcity.example") -> uuid.UUID:
    storage = build_adapters(settings).storage
    async with session_scope() as s:
        biz = Business(name="Hub City Plumbing", normalized_name="hub city plumbing", country="US",
                       domain="hubcity.example")  # fmt: skip
        s.add(biz)
        await s.flush()
        contact = Contact(business_id=biz.id, type="email", value=email, source_url="https://hubcity.example/contact",
                          verification_status="valid")  # fmt: skip
        s.add(contact)
        await s.flush()
        lead = Lead(business_id=biz.id, source="local_sites", source_url="https://hubcity.example/",
                    source_item_id=uuid.uuid4().hex, trigger_type="broken_or_outdated_site", country="US",
                    language="en", timezone="America/Chicago", status="qa_passed", recipient_type="corporate_entity",
                    pipeline_state={"contact_id": str(contact.id), "contact_channel": "email"})  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(Brief(lead_id=lead.id, data={"agents_to_run": ["contact_enrichment", "audit", "branding",
                    "quality_check", "proposal", "outreach"], "services": ["redesign"], "business_type": "plumber"}))  # fmt: skip
        ev = Evidence(
            lead_id=lead.id,
            kind="metric",
            url="https://hubcity.example/",
            content_hash=uuid.uuid4().hex,
        )
        s.add(ev)
        await s.flush()
        s.add(Audit(lead_id=lead.id, url="https://hubcity.example/", report_pdf_path=storage.save_bytes("a/audit.pdf", b"%PDF"),
                    findings=[{"id": "f1", "evidence_id": str(ev.id), "severity": "high", "title": "No mobile layout",
                               "detail": "The site has no viewport meta tag."}]))  # fmt: skip
        png = storage.save_bytes(f"logos/{lead.id.hex}/c1-512.png", b"\x89PNG")
        s.add(
            LogoConcept(
                lead_id=lead.id,
                variant=1,
                png_path=png,
                qa_status="passed",
                typography={"png_512": png},
            )
        )
        s.add(Preview(lead_id=lead.id, template_id="trades", qa_status="passed",
                      screenshots={"desktop": storage.save_bytes("p/d.png", b"\x89PNG")}, pdf_path=storage.save_bytes("p/p.pdf", b"%PDF")))  # fmt: skip
        return lead.id


async def _propose(settings: Settings, lead_id: uuid.UUID, adapters: Any = None) -> Any:
    adapters = adapters or build_adapters(settings)
    async with session_scope() as s:
        return await ProposalAgent().run(AgentContext(s, adapters, settings), lead_id)


async def _approve(lead_id: uuid.UUID) -> Proposal:
    async with session_scope() as s:
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
        p.approval_status, p.approved_by = "approved", "test"
        return p


async def _send(settings: Settings, lead_id: uuid.UUID, email_adapter: Any) -> Any:
    adapters = build_adapters(settings)
    adapters.email = email_adapter
    async with session_scope() as s:
        return await runs.execute(AgentContext(s, adapters, settings), OutreachAgent(), lead_id)


async def test_proposal_claims_map_to_evidence_and_wait_for_approval(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    outcome = await _propose(settings, lead_id)
    assert outcome.status == LeadStatus.AWAITING_APPROVAL and "approval" in outcome.reason
    async with session_scope() as s:
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
        ev_ids = {
            str(e)
            for e in (
                await s.execute(select(Evidence.id).where(Evidence.lead_id == lead_id))
            ).scalars()
        }
    assert p.approval_status == "pending" and p.to_email == "owner@hubcity.example"
    assert p.claims and all(c["evidence_id"] in ev_ids for c in p.claims)
    assert {a["kind"] for a in p.attachments} == {
        "logo",
        "preview_desktop",
        "preview_pdf",
        "audit_pdf",
    }
    assert "Test Agency" in p.body_text and "1 Test Street" in p.body_text and "/u/" in p.body_text
    assert p.draft["intro"] and p.draft["unsubscribe_url"].startswith("http")
    assert "track_b" not in p.draft  # email route: no manual drafts


async def test_proposal_track_b_manual_drafts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    async with session_scope() as s:
        biz = Business(name="Social Lead Co", normalized_name="social lead co", country="US")
        s.add(biz)
        await s.flush()
        contact = Contact(
            business_id=biz.id,
            type="platform_reply",
            value="https://x.com/post/1",
            source_url="https://x.com/post/1",
            verification_status="not_applicable",
        )
        s.add(contact)
        await s.flush()
        lead = Lead(
            business_id=biz.id,
            source="social_x",
            source_url="https://x.com/post/1",
            source_item_id=uuid.uuid4().hex,
            trigger_type="explicit_request",
            country="US",
            language="en",
            status="contact_found",
            pipeline_state={"contact_id": str(contact.id), "contact_channel": "platform_reply"},
        )
        s.add(lead)
        await s.flush()
        s.add(
            Brief(
                lead_id=lead.id,
                data={"agents_to_run": ["proposal", "outreach"], "services": ["website"]},
            )
        )
        await s.flush()
        lead_id = lead.id

    outcome = await _propose(settings, lead_id)
    assert outcome.status == LeadStatus.AWAITING_APPROVAL and "manual route" in outcome.reason
    async with session_scope() as s:
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
    assert p.to_email is None
    tb = p.draft["track_b"]
    assert tb["channel"] == "platform_reply" and tb["route"] == "https://x.com/post/1"
    assert tb["comment"] and tb["dm"] and tb["contact_form"]
    assert len(tb["comment"]) <= 280
    async with session_scope() as s:
        queue = (
            await s.execute(
                select(ManualOutreach).where(
                    ManualOutreach.lead_id == lead_id,
                    ManualOutreach.status == "pending",
                )
            )
        ).scalar_one()
    assert queue.channel == "platform_reply"
    assert queue.post_link == "https://x.com/post/1"


class InventingLLM:
    name: ClassVar[str] = "inventing"
    is_mock: ClassVar[bool] = True

    async def structured(self, session: Any, request: Any, schema: Any) -> Any:
        return ProposalDraft(subject="s", preview_text="p", greeting="Hi", intro="i", closing="c", signoff="b",
                             claims=[Claim(text="You lost 40% of customers last year", evidence_id=str(uuid.uuid4()))])  # fmt: skip


async def test_proposal_with_unmapped_claim_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    adapters = build_adapters(settings)
    adapters.llm = InventingLLM()  # type: ignore[assignment]
    outcome = await _propose(settings, lead_id, adapters)
    assert outcome.status == LeadStatus.NEEDS_REVIEW and "claims without" in outcome.reason
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Proposal))).scalar_one() == 0


async def test_stage_two_auto_approves(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    async with session_scope() as s:
        await runtime_settings.update(
            s, {"autonomy_stage": 2}, changed_by="test", allow_autonomy=True
        )
    outcome = await _propose(settings, lead_id)
    assert outcome.proceed and outcome.status == LeadStatus.APPROVED


async def test_sandbox_send_is_idempotent_with_unsubscribe_headers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    sandbox = SandboxEmail()
    outcome = await _send(settings, lead_id, sandbox)
    assert outcome.status == LeadStatus.SENT and "Mailpit" in outcome.reason
    email = sandbox.sent[0]
    assert email.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "/u/" in email.headers["List-Unsubscribe"] and email.reply_to.startswith("replies@")
    assert {a.filename for a in email.attachments} >= {"website-audit.pdf", "logo-concept-1.png"}
    again = await _send(settings, lead_id, sandbox)  # rerun never sends twice
    assert len(sandbox.sent) == 1 or not again.proceed
    async with session_scope() as s:
        msgs = (await s.execute(select(Message))).scalars().all()
        biz = (await s.execute(select(Business))).scalar_one()
    assert len(msgs) == 1 and msgs[0].sandbox and msgs[0].message_id_header
    assert biz.cooldown_until is not None


async def test_real_transport_sends_when_the_country_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inside the recipient's business hours whatever day the suite runs (weekends and nights
    # would otherwise schedule the send); the window itself is tested separately.
    from app.agents.outreach import agent as outreach_agent  # noqa: PLC0415

    monkeypatch.setattr(outreach_agent, "next_send_time", lambda now, *a, **k: now)
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    real = RealEmail()
    outcome = await _send(settings, lead_id, real)
    assert outcome.status == LeadStatus.SENT and len(real.sent) == 1


async def test_real_transport_never_sends_to_an_excluded_market(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        lead.country = "PK"
    real = RealEmail()
    outcome = await _send(settings, lead_id, real)
    assert not outcome.proceed and "PK is an excluded market" in outcome.reason and not real.sent


async def test_unsubscribe_link_suppresses_permanently(tmp_path: Path) -> None:
    from app.main import create_app  # noqa: PLC0415

    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    async with session_scope() as s:
        biz_id = (await s.get(Lead, lead_id)).business_id  # type: ignore[union-attr]
    token = make_token(settings, biz_id, "Owner@HubCity.example")
    assert read_token(settings, token).email == "owner@hubcity.example"  # type: ignore[union-attr]
    assert read_token(settings, token[:-2] + "xx") is None  # tampered
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get(f"/u/{token}")
        assert page.status_code == 200 and "owner@hubcity.example" in page.text
        done = await client.post(f"/u/{token}", content=b"List-Unsubscribe=One-Click")
        assert done.status_code == 200 and "unsubscribed" in done.text
        bad = await client.post("/u/not-a-token")
        assert bad.status_code == 400
    async with session_scope() as s:
        sup = (await s.execute(select(Suppression))).scalars().all()
        lead = await s.get(Lead, lead_id)
    assert sup and sup[0].reason == "unsubscribe" and lead.status == LeadStatus.SUPPRESSED  # type: ignore[union-attr]


async def test_replies_positive_to_inbox_and_unsubscribe_suppresses(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    await _send(settings, lead_id, SandboxEmail())
    async with session_scope() as s:
        msg = (await s.execute(select(Message))).scalar_one()
    adapters = build_adapters(settings)
    adapters.mailbox.items = {"INBOX": [  # type: ignore[attr-defined]
        MailItem(uid="1", message_id="<r1@hubcity.example>", from_email="owner@hubcity.example",
                 subject="Re: ideas", received_at=datetime.now(UTC), text="Sounds good, how much would it cost?",
                 in_reply_to=msg.message_id_header),
    ]}  # fmt: skip
    async with session_scope() as s:
        n = await outreach_jobs.ingest_replies(s, settings, adapters)
    assert n == 1
    async with session_scope() as s:
        reply = (await s.execute(select(Reply))).scalar_one()
        lead = await s.get(Lead, lead_id)
        deal = (await s.execute(select(Deal))).scalar_one_or_none()
    assert (
        reply.classification == "question" and reply.handed_to_inbox and reply.message_id == msg.id
    )
    assert lead.status == LeadStatus.REPLIED and deal is not None  # type: ignore[union-attr]

    adapters.mailbox.items = {"INBOX": [  # type: ignore[attr-defined]
        MailItem(uid="2", message_id="<r2@hubcity.example>", from_email="owner@hubcity.example",
                 subject="unsubscribe", received_at=datetime.now(UTC), text="unsubscribe")]}  # fmt: skip
    async with session_scope() as s:
        await outreach_jobs.ingest_replies(s, settings, adapters)
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Suppression))).scalar_one() == 1


async def test_follow_up_after_delay_threaded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    sandbox = SandboxEmail()
    await _send(settings, lead_id, sandbox)
    async with session_scope() as s:
        msg = (await s.execute(select(Message))).scalar_one()
        msg.sent_at = datetime.now(UTC) - timedelta(days=5)
        first_id = msg.message_id_header
    adapters = build_adapters(settings)
    adapters.email = sandbox
    async with session_scope() as s:
        assert await outreach_jobs.follow_ups(s, settings, adapters) == 1
    follow = sandbox.sent[-1]
    assert follow.headers.get("In-Reply-To") == first_id and not follow.attachments
    async with session_scope() as s:
        assert await outreach_jobs.follow_ups(s, settings, adapters) == 0  # not due yet


def test_sending_window_respects_weekends_and_holidays() -> None:
    sat = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)  # Saturday
    nxt = next_send_time(sat, "America/Chicago", "US")
    local = nxt.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    assert local.weekday() == 0 and local.hour == 9
    thanksgiving = datetime(2026, 11, 26, 16, 0, tzinfo=UTC)  # 10:00 in Chicago, US holiday
    assert not is_business_time(thanksgiving, "America/Chicago", "US")
    assert is_business_time(datetime(2026, 11, 24, 16, 0, tzinfo=UTC), "America/Chicago", "US")


async def test_autonomy_move_up_blocked_until_conditions_met() -> None:
    async with session_scope() as s:
        st = await autonomy.status(s)
        assert st.stage == 1 and not st.can_move_up
        with pytest.raises(PermissionError):
            await autonomy.change_stage(s, 2, changed_by="test", reason="try")
        with pytest.raises(PermissionError):
            await autonomy.change_stage(s, 3, changed_by="test", reason="skip")
        await runtime_settings.update(
            s, {"autonomy_stage": 2}, changed_by="test", allow_autonomy=True
        )
        down = await autonomy.change_stage(s, 1, changed_by="test", reason="back down")
        assert down.stage == 1


async def test_sandbox_still_enforces_recipient_type_rules(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        lead.recipient_type = "consumer"  # type: ignore[union-attr]
    await _propose(settings, lead_id)
    await _approve(lead_id)
    sandbox = SandboxEmail()
    outcome = await _send(settings, lead_id, sandbox)
    assert not outcome.proceed and "recipient type" in outcome.reason and not sandbox.sent
