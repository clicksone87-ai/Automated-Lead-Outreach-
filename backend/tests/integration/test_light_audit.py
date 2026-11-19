"""Light audit: static checks + PageSpeed, no browser, every finding bound to evidence."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.agents.audit import checks
from app.agents.audit.light import LightAuditAgent, looks_script_rendered, signal_findings
from app.config import Settings
from app.db.models import Audit, Brief, Business, Evidence, Lead, LeadStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SITE = "http://old-roofers.example.com/"
HOME = """<html><head><title>Old Roofers</title><script src="/js/jquery-1.4.2.min.js"></script></head>
<body><table width="800"><tr><td><table><tr><td><a href="/contact">Contact</a></td></tr></table>
</td></tr></table><table width="600"><tr><td>Menu</td></tr></table><h1>Old Roofers</h1><p>Roof repairs since 1990. Copyright 2012 Old Roofers.</p>
<a href="/gone">Gallery</a></body></html>"""


def _fetcher() -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path in ("/", ""):
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
        if request.url.path == "/contact":
            return httpx.Response(200, text="<p>call us</p>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="nope")

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


async def _lead() -> uuid.UUID:
    async with session_scope() as s:
        biz = Business(name="Old Roofers", normalized_name="old roofers", domain="old-roofers.example.com",
                       country="US", has_website=True)  # fmt: skip
        s.add(biz)
        await s.flush()
        lead = Lead(business_id=biz.id, source="local_sites", source_url=SITE, source_item_id=uuid.uuid4().hex,
                    trigger_type="broken_or_outdated_site", country="US", language="en",
                    status="contact_found", pipeline_state={"contact_id": str(uuid.uuid4())})  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(Brief(lead_id=lead.id, data={"agents_to_run": ["audit", "proposal"],
                                          "services": ["redesign", "seo"], "existing_website": SITE}))  # fmt: skip
        return lead.id


async def test_light_audit_finds_outdated_signals_without_a_browser() -> None:
    settings = Settings(_env_file=None, demo_mode=True, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher()

    def no_browser() -> None:
        raise AssertionError("the light audit must not start a browser")

    lead_id = await _lead()
    async with session_scope() as s:
        outcome = await runs.execute(
            AgentContext(s, adapters, settings), LightAuditAgent(no_browser), lead_id
        )
    assert outcome.proceed and outcome.status == LeadStatus.AUDITED, outcome.reason
    assert outcome.output["depth"] == "light"
    assert {"table_layout", "old_jquery"} <= set(outcome.output["signals"])
    async with session_scope() as s:
        audit = (await s.execute(select(Audit).where(Audit.lead_id == lead_id))).scalar_one()
        evidence_ids = {
            str(e)
            for e in (
                await s.execute(select(Evidence.id).where(Evidence.lead_id == lead_id))
            ).scalars()
        }
    assert audit.depth == "light" and audit.report_pdf_path is None and audit.screenshots == {}
    by_check = {f["check"]: f for f in audit.findings}
    assert {"table_layout", "old_jquery", "copyright_year", "broken_links"} <= set(by_check)
    assert all(f["evidence_id"] in evidence_ids for f in audit.findings)


def test_signal_findings_and_script_rendered_pages() -> None:
    drafts = signal_findings(["copyright_2015", "table_layout", "no_https"])
    assert [d.check for d in drafts] == [
        "copyright_year",
        "table_layout",
    ]  # no_https: security check
    spa = "<html><body><div id=root></div>" + "<script src=a.js></script>" * 3 + "</body></html>"
    assert looks_script_rendered(checks.page_facts(spa, "https://x.test/"), spa)
    assert not looks_script_rendered(checks.page_facts(HOME, SITE), HOME)
