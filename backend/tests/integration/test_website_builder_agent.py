"""Website builder agent with a fake build step (no Node in the test suite)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.adapters.registry import build_adapters
from app.agents.website_builder.agent import WebsiteBuilderAgent
from app.agents.website_builder.build import BuildError, BuildResult, PreviewTests
from app.config import Settings
from app.db.models import (
    BrandProfile,
    Brief,
    Business,
    EvidenceKind,
    Lead,
    LeadStatus,
    LogoConcept,
    Preview,
)
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext
from app.services import evidence

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

HOME = """<html><head><title>Hub City Plumbing</title></head><body>
<header><nav><a href="/services/">Services</a><a href="/about/">About</a></nav></header>
<p>Hub City Plumbing and Mechanical serves Lubbock with water heater repair and drain cleaning.
Call (806) 744-1234.</p></body></html>"""
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160"><text x="10" y="80">Hub City Plumbing</text></svg>'


def _settings(tmp: Path) -> Settings:
    return Settings(
        _env_file=None, demo_mode=True, data_dir=tmp, sender_business_name="Test Agency"
    )  # type: ignore[call-arg]


async def _lead(settings: Settings, *, agents: list[str] | None = None) -> uuid.UUID:
    adapters = build_adapters(settings)
    async with session_scope() as s:
        biz = Business(name="Hub City Plumbing and Mechanical", normalized_name="hub city plumbing",
                       domain="hubcityplumbing.com", country="US", industry="home_services",
                       address="Lubbock, TX", has_website=True)  # fmt: skip
        s.add(biz)
        await s.flush()
        lead = Lead(business_id=biz.id, source="local_sites", source_url="https://hubcityplumbing.com/",
                    source_item_id=uuid.uuid4().hex, trigger_type="broken_or_outdated_site",
                    country="US", language="en", status="branded",
                    pipeline_state={"contact_id": str(uuid.uuid4())})  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(Brief(lead_id=lead.id, data={
            "agents_to_run": agents or ["contact_enrichment", "audit", "branding", "website_builder",
                                        "quality_check", "proposal"],
            "services": ["redesign", "seo"], "existing_website": "https://hubcityplumbing.com/",
            "location": "Lubbock, TX", "business_type": "plumber"}))  # fmt: skip
        await evidence.capture(s, adapters.storage, kind=EvidenceKind.PAGE, lead_id=lead.id,
                               business_id=biz.id, url="https://hubcityplumbing.com/",
                               content=HOME.encode(), file_ext="html")  # fmt: skip
        s.add(BrandProfile(business_id=biz.id, brand_name="Hub City Plumbing and Mechanical",
                           colors=[{"hex": "#041e51", "role": "primary"}], fonts=[{"family": "Poppins"}]))  # fmt: skip
        svg_path = adapters.storage.save_bytes(f"logos/{lead.id.hex}/concept-1.svg", SVG.encode())
        s.add(LogoConcept(lead_id=lead.id, variant=1, svg_path=svg_path, png_path="x.png",
                          palette=["#0b5fa5", "#f59e0b"], similarity={"text_check": "exact"}))  # fmt: skip
        return lead.id


class FakeBuild:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.content: dict[str, Any] | None = None

    async def __call__(
        self,
        *,
        template_dir: Path,
        data_dir: Path,
        preview_id: str,
        content: dict[str, Any],
        cache_dir: Path | None = None,
    ) -> BuildResult:
        self.content = content
        if self.fail:
            raise BuildError("next build failed (1): Type error")
        site = data_dir / "previews" / preview_id / "site"
        site.mkdir(parents=True, exist_ok=True)
        (site / "index.html").write_text("<h1>Hub City Plumbing</h1>")
        work = data_dir / "builds" / preview_id
        work.mkdir(parents=True, exist_ok=True)
        log = data_dir / "builds" / f"{preview_id}.log"
        log.write_text("ok")
        return BuildResult(site, work, "abc123", log, 12.5)


async def fake_tests(
    site: Path, *, expected_name: str, **_: Any
) -> tuple[PreviewTests, dict[str, bytes]]:
    checks = [{"name": "business_name", "passed": True, "detail": expected_name}]
    return PreviewTests(True, checks, {}, {"performance": 95.0}), {
        "desktop.png": b"png",
        "preview.pdf": b"%PDF",
    }


async def test_builds_redesign_preview_with_provenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    build = FakeBuild()
    adapters = build_adapters(settings)
    async with session_scope() as s:
        outcome = await runs.execute(
            AgentContext(s, adapters, settings),
            WebsiteBuilderAgent(builder=build, tester=fake_tests),
            lead_id,
        )
    assert outcome.status == LeadStatus.BUILT, outcome.reason
    c = build.content
    assert c is not None
    assert c["mode"] == "redesign" and c["variant"] == "classic"
    assert [p["slug"] for p in c["pages"]] == ["", "services", "about"]  # original structure
    assert c["business"]["name"] == "Hub City Plumbing and Mechanical"
    assert c["business"]["phone"] == "(806) 744-1234"  # from the client's own site text
    assert c["brand"]["logoSvg"].startswith("<svg") and c["brand"]["primary"] == "#0b5fa5"
    async with session_scope() as s:
        p = (await s.execute(select(Preview).where(Preview.lead_id == lead_id))).scalar_one()
    assert p.preview_url == f"http://{lead_id.hex}.preview.localhost:8080/"
    assert p.git_commit == "abc123" and p.qa_status == "pending"
    assert p.reused_content[0]["ownership"] == "the client's own public website"
    assert p.screenshots["desktop"].endswith("screens/desktop.png") and p.pdf_path.endswith(
        "preview.pdf"
    )
    assert p.test_results["passed"] is True
    # the client's site text reached the LLM only inside the untrusted DATA block
    llm = adapters.llm
    req = next(r for r, _ in llm.calls if r.agent == "website_builder")  # type: ignore[attr-defined]
    assert "current_website_text" in req.untrusted and "water heater" not in req.task


async def test_build_failure_goes_to_review(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    async with session_scope() as s:
        outcome = await runs.execute(
            AgentContext(s, build_adapters(settings), settings),
            WebsiteBuilderAgent(builder=FakeBuild(fail=True), tester=fake_tests),
            lead_id,
        )
    assert not outcome.proceed and outcome.status == LeadStatus.NEEDS_REVIEW
    async with session_scope() as s:
        p = (await s.execute(select(Preview).where(Preview.lead_id == lead_id))).scalar_one()
    assert p.qa_status == "failed" and "Type error" in p.test_results["build_error"]


async def test_skips_when_not_needed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings, agents=["contact_enrichment", "branding", "proposal"])
    async with session_scope() as s:
        outcome = await WebsiteBuilderAgent(builder=FakeBuild(), tester=fake_tests).run(
            AgentContext(s, build_adapters(settings), settings), lead_id
        )
    assert outcome.proceed and "not needed" in outcome.reason


class TimeoutBuild(FakeBuild):
    async def __call__(self, **kw: Any) -> BuildResult:
        raise BuildError("next build failed (124): timed out after 900s", timed_out=True)


async def test_build_timeout_is_retried_not_reviewed(tmp_path: Path) -> None:
    from app.orchestrator.errors import TransientAgentError  # noqa: PLC0415

    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    with pytest.raises(TransientAgentError):
        async with session_scope() as s:
            await runs.execute(
                AgentContext(s, build_adapters(settings), settings),
                WebsiteBuilderAgent(builder=TimeoutBuild(), tester=fake_tests),
                lead_id,
            )
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead.status != LeadStatus.NEEDS_REVIEW  # type: ignore[union-attr]
