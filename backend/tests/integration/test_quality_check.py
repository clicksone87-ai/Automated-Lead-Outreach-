"""Quality check (3.12–3.13): logos, preview, evidence links; no self-approval; returns."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.adapters.registry import build_adapters
from app.config import Settings
from app.db.models import (
    AgentRun,
    Audit,
    Brief,
    Business,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
    Preview,
    QualityCheck,
)
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext
from app.quality.agent import QualityCheckAgent

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160"><rect width="80" height="80" fill="#123"/><text x="100" y="80">Hub City Plumbing</text></svg>'
PASSING_CHECKS = [
    {"name": n, "passed": True, "detail": ""}
    for n in ("build_output", "title", "h1", "business_name", "cta", "viewport_meta", "noindex",
              "internal_links", "renders", "no_external_requests")
]  # fmt: skip


def _png(width: int = 1024) -> bytes:
    img = Image.new("RGBA", (width, width // 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 10, 200, 200), fill=(20, 40, 200, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _settings(tmp: Path) -> Settings:
    return Settings(_env_file=None, demo_mode=True, data_dir=tmp)  # type: ignore[call-arg]


async def _lead(settings: Settings, *, similar: bool = False, preview_ok: bool = True,
                evidence_ok: bool = True) -> uuid.UUID:  # fmt: skip
    storage = build_adapters(settings).storage
    async with session_scope() as s:
        biz = Business(name="Hub City Plumbing", normalized_name="hub city plumbing", country="US")
        s.add(biz)
        await s.flush()
        lead = Lead(business_id=biz.id, source="local_sites", source_url="https://h.example/",
                    source_item_id=uuid.uuid4().hex, trigger_type="broken_or_outdated_site",
                    country="US", language="en", status="built", pipeline_state={})  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(
            Brief(
                lead_id=lead.id,
                data={"agents_to_run": ["branding", "website_builder", "quality_check"]},
            )
        )
        ev = Evidence(
            lead_id=lead.id, kind="page", url="https://h.example/", content_hash=uuid.uuid4().hex
        )
        s.add(ev)
        await s.flush()
        finding_ev = str(ev.id) if evidence_ok else str(uuid.uuid4())
        s.add(Audit(lead_id=lead.id, url="https://h.example/",
                    findings=[{"id": "f1", "evidence_id": finding_ev, "title": "No HTTPS"}]))  # fmt: skip
        hexid = lead.id.hex
        svg_path = storage.save_bytes(f"logos/{hexid}/concept-1.svg", SVG.encode())
        png_path = storage.save_bytes(f"logos/{hexid}/concept-1-1024.png", _png())
        s.add(LogoConcept(lead_id=lead.id, variant=1, svg_path=svg_path, png_path=png_path,
                          text="Hub City Plumbing",
                          similarity={"too_similar": similar, "nearest": "rival.png", "distance": 0.05 if similar else 0.6,
                                      "checked_against": 1}))  # fmt: skip
        checks = [*PASSING_CHECKS]
        if not preview_ok:
            checks[3] = {"name": "business_name", "passed": False, "detail": "name missing"}
        s.add(Preview(lead_id=lead.id, template_id="trades", test_results={"passed": preview_ok, "checks": checks},
                      screenshots={"desktop": "d.png", "mobile": "m.png"}))  # fmt: skip
        for agent in ("branding", "website_builder"):
            s.add(AgentRun(agent=agent, lead_id=lead.id, status="succeeded", attempts=1,
                           finished_at=datetime.now(UTC)))  # fmt: skip
        return lead.id


async def _run(settings: Settings, lead_id: uuid.UUID) -> Any:
    async with session_scope() as s:
        return await runs.execute(
            AgentContext(s, build_adapters(settings), settings), QualityCheckAgent(), lead_id
        )


async def test_all_good_passes_and_records_every_verdict(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings)
    outcome = await _run(settings, lead_id)
    assert outcome.status == LeadStatus.QA_PASSED, outcome.reason
    async with session_scope() as s:
        logo = (
            await s.execute(select(LogoConcept).where(LogoConcept.lead_id == lead_id))
        ).scalar_one()
        preview = (await s.execute(select(Preview).where(Preview.lead_id == lead_id))).scalar_one()
        qcs = (
            (await s.execute(select(QualityCheck).where(QualityCheck.lead_id == lead_id)))
            .scalars()
            .all()
        )
    assert logo.qa_status == "passed" and preview.qa_status == "passed"
    assert {q.item_type for q in qcs} == {"logo", "preview", "audit"}
    assert all(q.checker == "quality_check" and q.producer != q.checker for q in qcs)


async def test_near_copy_logo_returns_to_branding_once_then_human(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings, similar=True)
    first = await _run(settings, lead_id)
    assert first.status == LeadStatus.AUDITED and "returned to branding" in first.reason
    async with session_scope() as s:
        statuses = (await s.execute(select(AgentRun.status).where(
            AgentRun.lead_id == lead_id, AgentRun.agent == "branding"))).scalars().all()  # fmt: skip
        assert "superseded" in statuses
        # branding "re-ran" and produced another near-copy
        s.add(
            AgentRun(
                agent="branding",
                lead_id=lead_id,
                status="succeeded",
                attempts=2,
                finished_at=datetime.now(UTC),
            )
        )
        qa_run = (await s.execute(select(AgentRun).where(
            AgentRun.lead_id == lead_id, AgentRun.agent == "quality_check"))).scalar_one()  # fmt: skip
        await s.delete(qa_run)
    second = await _run(settings, lead_id)
    assert second.status == LeadStatus.NEEDS_REVIEW and "human review" in second.reason


async def test_failing_preview_returns_to_builder(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings, preview_ok=False)
    outcome = await _run(settings, lead_id)
    assert outcome.status == LeadStatus.BRANDED and "website_builder" in outcome.reason


async def test_finding_without_evidence_needs_review(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lead_id = await _lead(settings, evidence_ok=False)
    outcome = await _run(settings, lead_id)
    assert outcome.status == LeadStatus.NEEDS_REVIEW and "evidence" in outcome.reason
