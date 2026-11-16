"""5.06 — one lead through the FULL pipeline with mocked adapters:
requirements → contact → audit → branding → website builder → quality check → proposal →
(human approval) → outreach. Real agents, real policy, recorded fixtures, no network."""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.adapters.email import DeliveryEvent, OutgoingEmail
from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.agents.audit.agent import AuditAgent
from app.agents.audit.browser import GuardedBrowser
from app.agents.branding.agent import BrandingAgent
from app.agents.contact_enrichment.agent import ContactEnrichmentAgent
from app.agents.outreach.agent import OutreachAgent
from app.agents.proposal.agent import ProposalAgent
from app.agents.requirements.agent import RequirementsAgent
from app.agents.website_builder.agent import WebsiteBuilderAgent
from app.agents.website_builder.build import BuildResult, PreviewTests
from app.config import Settings
from app.db.models import Business, Lead, LeadStatus, Message, Proposal
from app.db.session import session_scope
from app.orchestrator.pipeline import build_graph
from app.quality.agent import QualityCheckAgent

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SITE = "https://acme.example.com/"
HOME = """<html><head><title>Acme Plumbing Ltd</title><meta name="theme-color" content="#1d4ed8"></head>
<body><table width="600"><tr><td><table><tr><td><a href="/contact">Contact</a></td></tr></table></td></tr></table>
<h1>Acme Plumbing</h1><p>Plumbing repairs in Austin. Copyright 2014.</p></body></html>"""
CONTACT = (
    '<html><body><a href="mailto:info@acme.example.com">info@acme.example.com</a></body></html>'
)


def _png(w: int = 1024, h: int = 512) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste((29, 78, 216, 255), (40, 40, 300, 300))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _fetcher() -> SafeFetcher:
    pages = {SITE: HOME, f"{SITE}contact": CONTACT}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        body = pages.get(str(request.url))
        return (
            httpx.Response(200, text=body, headers={"content-type": "text/html"})
            if body
            else httpx.Response(404)
        )

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


class FakeRender:
    async def __aenter__(self) -> FakeRender:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def render_png(
        self, html: str, *, width: int, height: int, transparent: bool = True
    ) -> bytes:
        return _png(width, height)


async def fake_build(
    *, template_dir: Path, data_dir: Path, preview_id: str, content: dict[str, Any]
) -> BuildResult:
    site = data_dir / "previews" / preview_id / "site"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(f"<h1>{content['business']['name']}</h1>")
    log = data_dir / "builds" / f"{preview_id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("ok")
    return BuildResult(site, log.parent, "abc", log, 3.0)


async def fake_tests(site: Path, *, expected_name: str) -> tuple[PreviewTests, dict[str, bytes]]:
    names = ("build_output", "title", "h1", "business_name", "cta", "viewport_meta", "noindex",
             "internal_links", "renders", "no_external_requests")  # fmt: skip
    return PreviewTests(True, [{"name": n, "passed": True, "detail": ""} for n in names]), {
        "desktop.png": _png(1366, 900), "mobile.png": _png(390, 844), "preview.pdf": b"%PDF-1.4",
    }  # fmt: skip


class Mailpit:
    name: ClassVar[str] = "mailpit_fixture"
    is_mock: ClassVar[bool] = False
    is_sandbox = True

    def __init__(self) -> None:
        self.inbox: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> str:
        self.inbox.append(email)
        return f"<{uuid.uuid4().hex}@test-agency.local>"

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="delivered")


async def test_one_lead_through_the_whole_pipeline(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, demo_mode=True, data_dir=tmp_path, sender_name="Sam",  # type: ignore[call-arg]
                        sender_business_name="Test Agency", sender_postal_address="1 Test St",
                        portfolio_url="https://example.com")  # fmt: skip
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher()
    mailpit = Mailpit()
    adapters.email = mailpit
    agents = {
        "requirements": RequirementsAgent(),
        "contact_enrichment": ContactEnrichmentAgent(),
        "audit": AuditAgent(
            browser_factory=lambda: GuardedBrowser(fulfill={SITE: (200, "text/html", HOME)})
        ),
        "branding": BrandingAgent(browser_factory=FakeRender),
        "website_builder": WebsiteBuilderAgent(builder=fake_build, tester=fake_tests),
        "quality_check": QualityCheckAgent(),
        "proposal": ProposalAgent(),
        "outreach": OutreachAgent(),
    }
    async with session_scope() as s:
        biz = Business(name="Acme Plumbing Ltd", normalized_name="acme plumbing", domain="acme.example.com",
                       country="US", address="Austin, TX", industry="home_services", has_website=True)  # fmt: skip
        s.add(biz)
        await s.flush()
        lead = Lead(business_id=biz.id, source="rss", source_url="https://forum.example.com/t/1",
                    source_item_id=uuid.uuid4().hex, trigger_type="explicit_request", country="US",
                    language="en", timezone="America/Chicago", status="discovered", pipeline_state={},
                    title="Need a website redesign and a new logo",
                    raw_text="We need a website redesign and a new logo for our plumbing business. Budget $2000, asap.")  # fmt: skip
        s.add(lead)
        await s.flush()
        lead_id = lead.id

    graph = build_graph(agents, adapters, settings)
    first = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    assert first["stopped_at"] == "proposal", first  # Stage 1: waits for a human
    assert first["completed"] == list(agents)[:7]
    async with session_scope() as s:
        lead_row = await s.get(Lead, lead_id)
        proposal = (
            await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))
        ).scalar_one()
        assert lead_row.status == LeadStatus.AWAITING_APPROVAL  # type: ignore[union-attr]
        from app.db.models import AgentRun, Audit  # noqa: PLC0415

        audit = (
            await s.execute(select(Audit).where(Audit.lead_id == lead_id))
        ).scalar_one_or_none()
        runs_ = {
            r.agent: r.output
            for r in (
                await s.execute(select(AgentRun).where(AgentRun.lead_id == lead_id))
            ).scalars()
        }
        assert audit is not None and audit.findings, runs_.get("audit")
        assert proposal.to_email == "info@acme.example.com" and proposal.claims
        proposal.approval_status, proposal.approved_by = "approved", "test"  # the human approves

    second = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    assert second["stopped_at"] is None, second
    async with session_scope() as s:
        lead_row = await s.get(Lead, lead_id)
        msg = (await s.execute(select(Message))).scalar_one()
    assert lead_row.status == LeadStatus.SENT and msg.sandbox  # type: ignore[union-attr]
    email = mailpit.inbox[0]
    assert email.to == "info@acme.example.com" and "List-Unsubscribe" in email.headers
    assert {a.filename for a in email.attachments} >= {"website-audit.pdf", "website-preview.pdf"}
