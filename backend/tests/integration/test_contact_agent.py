"""Contact Enrichment agent (Agent 3) against Postgres with fixture websites."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.apify import ApifyRun
from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.adapters.verification import VerificationResult
from app.agents.contact_enrichment.agent import ContactEnrichmentAgent
from app.config import Settings
from app.db.models import Business, Contact, Evidence, Lead, LeadStatus, VerificationStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

HOME = """<html><body><nav><a href="/contact">Contact us</a><a href="/about">About</a></nav>
<p>Family plumbers since 1998.</p></body></html>"""
CONTACT = """<html><body><a href="mailto:info@acme-plumbing.com">info@acme-plumbing.com</a>
<p>Or the owner: dave.acme@gmail.com</p>
<form action="/send"><input name="email"><textarea name="message"></textarea></form></body></html>"""
ABOUT = "<html><body><p>Dave started Acme in 1998.</p></body></html>"


class FakeVerifier:
    name: ClassVar[str] = "fake_verifier"
    is_mock: ClassVar[bool] = False

    def __init__(self, results: dict[str, VerificationStatus]) -> None:
        self.results = results
        self.calls: list[str] = []

    async def verify(self, session: Any, email: str, *, agent: str) -> VerificationResult:
        self.calls.append(email)
        status = self.results.get(email, VerificationStatus.UNKNOWN)
        return VerificationResult(email=email, status=status, provider_status=str(status))


def _fetcher(pages: dict[str, str]) -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


async def _setup(
    *,
    domain: str | None,
    text: str = "Need a new website",
    source: str = "rss",
    trigger: str = "explicit_request",
) -> uuid.UUID:
    async with session_scope() as s:
        biz = Business(
            name="Acme Plumbing",
            normalized_name="acme plumbing",
            domain=domain,
            country="US",
            has_website=bool(domain),
        )
        s.add(biz)
        await s.flush()
        lead = Lead(
            business_id=biz.id,
            source=source,
            source_url="https://forum.test/t/1",
            source_item_id=uuid.uuid4().hex,
            trigger_type=trigger,
            title="Website help",
            raw_text=text,
            country="US",
            language="en",
            pipeline_state={},
        )
        s.add(lead)
        await s.flush()
        return lead.id


async def _run(lead_id: uuid.UUID, pages: dict[str, str], verifier: FakeVerifier) -> Any:
    settings = Settings(_env_file=None, demo_mode=False, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher(pages)
    adapters.verification = verifier
    async with session_scope() as s:
        return await runs.execute(
            AgentContext(s, adapters, settings), ContactEnrichmentAgent(), lead_id
        )


async def test_finds_and_verifies_best_email_with_source_url() -> None:
    lead_id = await _setup(domain="acme-plumbing.com")
    verifier = FakeVerifier({"info@acme-plumbing.com": VerificationStatus.VALID})
    pages = {
        "https://acme-plumbing.com/": HOME,
        "https://acme-plumbing.com/contact": CONTACT,
        "https://acme-plumbing.com/about": ABOUT,
    }
    outcome = await _run(lead_id, pages, verifier)
    assert outcome.proceed and outcome.status == LeadStatus.CONTACT_FOUND
    assert verifier.calls == ["info@acme-plumbing.com"]  # best-ranked first, stop at valid
    async with session_scope() as s:
        email = (
            await s.execute(
                select(Contact).where(
                    Contact.type == "email", Contact.value == "info@acme-plumbing.com"
                )
            )
        ).scalar_one()
        assert email.source_url == "https://acme-plumbing.com/contact"
        assert email.verification_status == "valid" and email.verified_at is not None
        form = (await s.execute(select(Contact).where(Contact.type == "contact_form"))).scalar_one()
        assert form.value == "https://acme-plumbing.com/send"
        lead = await s.get(Lead, lead_id)
        assert lead.pipeline_state["contact_channel"] == "email"  # type: ignore[union-attr]
        assert (await s.execute(select(func.count()).select_from(Evidence))).scalar_one() == 1


async def test_never_guesses_an_address_when_site_publishes_none() -> None:
    lead_id = await _setup(domain="acme-plumbing.com", source="web_directory", trigger="no_website")
    verifier = FakeVerifier({})
    pages = {"https://acme-plumbing.com/": HOME, "https://acme-plumbing.com/about": ABOUT}
    outcome = await _run(lead_id, pages, verifier)
    assert outcome.status == LeadStatus.NO_CONTACT_ROUTE
    assert verifier.calls == []
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Contact))).scalar_one() == 0


async def test_invalid_emails_are_never_used() -> None:
    lead_id = await _setup(domain="acme-plumbing.com", source="web_directory", trigger="no_website")
    verifier = FakeVerifier(
        {
            "info@acme-plumbing.com": VerificationStatus.INVALID,
            "dave.acme@gmail.com": VerificationStatus.UNKNOWN,
        }
    )
    contact_no_form = CONTACT.split("<form", maxsplit=1)[0]
    pages = {
        "https://acme-plumbing.com/": HOME,
        "https://acme-plumbing.com/contact": contact_no_form,
    }
    outcome = await _run(lead_id, pages, verifier)
    assert outcome.status == LeadStatus.NO_CONTACT_ROUTE
    async with session_scope() as s:
        statuses = set((await s.execute(select(Contact.verification_status))).scalars())
        assert statuses == {"invalid", "unknown"}


async def test_platform_post_gets_manual_reply_route() -> None:
    lead_id = await _setup(domain=None, source="reddit")
    outcome = await _run(lead_id, {}, FakeVerifier({}))
    assert outcome.proceed
    assert "manual route" in outcome.reason
    async with session_scope() as s:
        route = (await s.execute(select(Contact))).scalar_one()
        assert (route.type, route.value, route.verification_status) == (
            "platform_reply",
            "https://forum.test/t/1",
            "not_applicable",
        )


async def test_email_published_in_post_is_used_and_rerun_is_idempotent() -> None:
    lead_id = await _setup(domain=None, text="Need a website. Contact me: owner@riverside-co.com")
    verifier = FakeVerifier({"owner@riverside-co.com": VerificationStatus.VALID})
    first = await _run(lead_id, {}, verifier)
    assert first.status == LeadStatus.CONTACT_FOUND
    again = await _run(lead_id, {}, verifier)
    assert again.reason == "already completed"
    async with session_scope() as s:
        c = (await s.execute(select(Contact))).scalar_one()
        assert c.source_url == "https://forum.test/t/1"


class _FakeApify:
    """Stands in for the Apify client: one public Instagram profile."""

    def __init__(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_actor(
        self, session: Any, actor: str, actor_input: dict[str, Any], **kw: Any
    ) -> Any:
        self.calls.append((actor, actor_input))
        return ApifyRun("r1", "SUCCEEDED", [self.profile], 0.0026)


async def test_instagram_bio_route_uses_published_email_and_site() -> None:
    lead_id = await _setup(
        domain=None, text="Looking for someone to design a logo", source="social_instagram"
    )
    async with session_scope() as s:
        s.add(Evidence(lead_id=lead_id, kind="post", url="https://www.instagram.com/p/x/",
                       content_hash=uuid.uuid4().hex, data={"handle": "acme_plumbing"}))  # fmt: skip
    apify = _FakeApify(
        {
            "username": "acme_plumbing",
            "biography": "Family plumbers. Bookings: hello@acme-plumbing.com",
            "externalUrl": "https://acme-plumbing.com/",
        }
    )
    verifier = FakeVerifier({"hello@acme-plumbing.com": VerificationStatus.VALID})
    settings = Settings(_env_file=None, demo_mode=False, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher({"https://acme-plumbing.com/": HOME})
    adapters.verification = verifier
    adapters.apify = apify  # type: ignore[assignment]
    async with session_scope() as s:
        outcome = await runs.execute(
            AgentContext(s, adapters, settings), ContactEnrichmentAgent(), lead_id
        )
    assert outcome.status == LeadStatus.CONTACT_FOUND
    assert apify.calls == [("apify/instagram-profile-scraper", {"usernames": ["acme_plumbing"]})]
    async with session_scope() as s:
        email = (await s.execute(select(Contact).where(Contact.type == "email"))).scalar_one()
        assert email.value == "hello@acme-plumbing.com"
        assert email.source_url == "https://www.instagram.com/acme_plumbing/"
        lead = await s.get(Lead, lead_id)
        biz = await s.get(Business, lead.business_id)  # type: ignore[union-attr]
        assert biz.domain == "acme-plumbing.com"  # type: ignore[union-attr]
