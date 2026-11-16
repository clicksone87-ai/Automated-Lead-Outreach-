"""Audit agent end to end: fixture site, real headless Chromium (no network), Postgres."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.agents.audit.agent import AuditAgent
from app.agents.audit.browser import GuardedBrowser
from app.config import Settings
from app.db.models import Audit, Brief, Business, Evidence, Lead, LeadStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SITE = "https://acme-plumbing.com/"
HOME = """<html><head><title>Acme</title></head><body style="font-family:serif">
<h1>Acme Plumbing</h1><img src="http://acme-plumbing.com/banner.gif">
<p>Welcome to our web site. We do plumbing.</p><a href="/old-page">Old page</a>
<a href="/about">About</a></body></html>"""


def _fetcher() -> SafeFetcher:
    pages = {SITE: HOME, "https://acme-plumbing.com/about": "<p>about</p>"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url.startswith("http://acme-plumbing.com"):
            return httpx.Response(
                200, text=HOME, headers={"content-type": "text/html"}
            )  # no redirect
        if url in pages:
            return httpx.Response(200, text=pages[url], headers={"content-type": "text/html"})
        return httpx.Response(404, text="nope")

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        demo_mode=True,
        data_dir=Path("./.test-data"),  # type: ignore[call-arg]
        sender_business_name="Test Studio",
    )


async def _lead(agents: list[str]) -> uuid.UUID:
    async with session_scope() as s:
        biz = Business(
            name="Acme Plumbing",
            normalized_name="acme plumbing",
            domain="acme-plumbing.com",
            country="US",
            address="Austin, TX 78704",
            industry="home_services",
            has_website=True,
        )
        s.add(biz)
        await s.flush()
        lead = Lead(
            business_id=biz.id,
            source="rss",
            source_url="https://forum.test/1",
            source_item_id=uuid.uuid4().hex,
            trigger_type="explicit_request",
            country="US",
            language="en",
            status="contact_found",
            pipeline_state={"contact_id": str(uuid.uuid4())},
        )
        s.add(lead)
        await s.flush()
        s.add(
            Brief(
                lead_id=lead.id,
                data={
                    "agents_to_run": agents,
                    "services": ["redesign", "seo"],
                    "existing_website": SITE,
                    "location": "Austin, TX",
                    "business_type": "plumber",
                },
            )
        )
        return lead.id


async def _run(lead_id: uuid.UUID) -> object:
    settings = _settings()
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher()
    agent = AuditAgent(
        browser_factory=lambda: GuardedBrowser(fulfill={SITE: (200, "text/html", HOME)})
    )
    async with session_scope() as s:
        return await runs.execute(AgentContext(s, adapters, settings), agent, lead_id)


async def test_full_audit_stores_report_with_evidence_for_every_finding() -> None:
    lead_id = await _lead(["contact_enrichment", "audit", "proposal"])
    outcome = await _run(lead_id)
    assert outcome.proceed, outcome.reason  # type: ignore[attr-defined]
    assert outcome.status == LeadStatus.AUDITED  # type: ignore[attr-defined]
    async with session_scope() as s:
        audit = (await s.execute(select(Audit).where(Audit.lead_id == lead_id))).scalar_one()
        checks = {f["check"] for f in audit.findings}
        assert {
            "viewport_missing",
            "meta_description_missing",
            "mixed_content",
            "cta_missing",
            "broken_links",
            "https_redirect",
            "perf_low",
        } <= checks
        assert any(c.startswith("design_") for c in checks)  # vision critique (mock)
        evidence_ids = {
            str(e)
            for e in (
                await s.execute(select(Evidence.id).where(Evidence.lead_id == lead_id))
            ).scalars()
        }
        assert all(f["evidence_id"] in evidence_ids for f in audit.findings)
        kinds = set(
            (await s.execute(select(Evidence.kind).where(Evidence.lead_id == lead_id))).scalars()
        )
        assert {"page", "metric", "screenshot"} <= kinds
        assert audit.screenshots["desktop"] and audit.screenshots["mobile"]
        assert audit.scores["design"] == 48.0
        assert audit.before_after_summary.startswith("Today:")
        assert audit.report_pdf_path and audit.report_pdf_path.endswith("report.pdf")
        pdf = (Path("./.test-data") / audit.report_pdf_path).read_bytes()
        assert pdf.startswith(b"%PDF")
        biz = (await s.execute(select(Business))).scalar_one()
        assert biz.site_status == "outdated"
        broken = next(f for f in audit.findings if f["check"] == "broken_links")
        assert broken["metric"]["broken"][0]["url"] == "https://acme-plumbing.com/old-page"


class _CompetitorSearch:
    name = "fake_search"
    is_mock = False

    async def search(self, session: object, query: str, **kw: object) -> list[object]:
        from app.adapters.search import SearchResult  # noqa: PLC0415

        return [
            SearchResult(title="Rival", url="https://rival-plumbing.com/"),
            SearchResult(title="Yelp", url="https://www.yelp.com/biz/x"),
            SearchResult(title="Self", url=SITE),
        ]


async def test_competitors_and_axe_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_axe = tmp_path / "axe.min.js"
    fake_axe.write_text(
        "window.axe = {run: async () => ({violations: [{id: 'color-contrast', impact: 'serious',"
        " help: 'Elements must have sufficient color contrast', nodes: [{target: ['p']}]}]})};",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.agents.audit.browser.AXE_CANDIDATES", (str(fake_axe),))
    lead_id = await _lead(["contact_enrichment", "audit", "proposal"])
    rival = (
        '<html><head><meta name="viewport" content="width=device-width"><title>Rival</title>'
        '<script type="application/ld+json">{"@type":"Plumber"}</script></head>'
        '<body><a href="/quote">Get a quote</a></body></html>'
    )
    base = _fetcher()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rival-plumbing.com" and request.url.path == "/":
            return httpx.Response(200, text=rival, headers={"content-type": "text/html"})
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return (
            httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
            if request.url.host == "acme-plumbing.com" and request.url.path == "/"
            else httpx.Response(404)
        )

    settings = _settings()
    adapters = build_adapters(settings)
    adapters.fetcher = SafeFetcher(
        "TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0
    )
    adapters.search = _CompetitorSearch()  # type: ignore[assignment]
    agent = AuditAgent(
        browser_factory=lambda: GuardedBrowser(fulfill={SITE: (200, "text/html", HOME)})
    )
    async with session_scope() as s:
        outcome = await runs.execute(AgentContext(s, adapters, settings), agent, lead_id)
    assert outcome.proceed, outcome.reason
    await base.aclose()
    async with session_scope() as s:
        audit = (await s.execute(select(Audit).where(Audit.lead_id == lead_id))).scalar_one()
        assert [c["domain"] for c in audit.competitor_comparison] == ["rival-plumbing.com"]
        rival_row = audit.competitor_comparison[0]
        assert (
            rival_row["mobile_ready"] and rival_row["has_cta"] and rival_row["has_structured_data"]
        )
        axe = next(f for f in audit.findings if f["check"] == "axe_color-contrast")
        assert axe["severity"] == "medium"


async def test_unreachable_site_is_a_critical_finding_with_evidence() -> None:
    lead_id = await _lead(["contact_enrichment", "audit", "proposal"])
    settings = _settings()
    adapters = build_adapters(settings)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    adapters.fetcher = SafeFetcher(
        "TestBot/1.0", transport=httpx.MockTransport(down), min_interval_s=0
    )
    async with session_scope() as s:
        outcome = await runs.execute(AgentContext(s, adapters, settings), AuditAgent(), lead_id)
    assert outcome.proceed and outcome.status == LeadStatus.AUDITED
    async with session_scope() as s:
        audit = (await s.execute(select(Audit).where(Audit.lead_id == lead_id))).scalar_one()
        assert [f["check"] for f in audit.findings] == ["site_unreachable"]
        ev = await s.get(Evidence, uuid.UUID(audit.findings[0]["evidence_id"]))
        assert ev is not None and "ConnectError" in ev.data["error"]
        assert (await s.execute(select(Business))).scalar_one().site_status == "unreachable"


async def test_audit_skipped_when_brief_does_not_need_it() -> None:
    lead_id = await _lead(["contact_enrichment", "branding", "proposal"])
    outcome = await _run(lead_id)
    assert outcome.proceed  # type: ignore[attr-defined]
    assert outcome.reason == "audit not needed for this lead"  # type: ignore[attr-defined]
    async with session_scope() as s:
        assert (await s.execute(select(Audit))).first() is None
