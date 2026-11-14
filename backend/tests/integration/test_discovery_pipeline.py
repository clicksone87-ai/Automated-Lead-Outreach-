"""Discovery runner, pipeline runner and API — end to end on Postgres with mock adapters."""

from __future__ import annotations

import uuid
from typing import ClassVar

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.registry import get_adapters
from app.agents.discovery.runner import ensure_source_rows, run_source
from app.config import get_settings
from app.db.models import AgentRun, Business, Evidence, Lead, RunStatus, Source
from app.db.session import session_scope
from app.orchestrator import agents as agent_registry
from app.orchestrator import pipeline
from app.orchestrator.agents import AgentContext, AgentOutcome

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _run_demo() -> list[uuid.UUID]:
    async with session_scope() as s:
        await ensure_source_rows(s, get_settings())
        row = await s.get(Source, "mock_demo")
        assert row is not None
        row.config = {"per_run": 8}
    async with session_scope() as s:
        result = await run_source(s, get_adapters(), get_settings(), "mock_demo")
    assert result.status == "succeeded"
    return result.lead_ids or []


async def test_demo_discovery_creates_tagged_leads_with_evidence_and_locale() -> None:
    lead_ids = await _run_demo()
    assert len(lead_ids) == 8
    async with session_scope() as s:
        leads = (await s.execute(select(Lead))).scalars().all()
        assert all(lead.pipeline_state.get("mock_adapters_used") for lead in leads)
        by_name = {b.name: b for b in (await s.execute(select(Business))).scalars().all()}
        assert by_name["Riverside Plumbing Co"].domain == "riverside-plumbing.example.com"
        austin = next(lead for lead in leads if "Austin" in (lead.raw_text or ""))
        assert (austin.country, austin.timezone) == ("US", "America/Chicago")
        berlin = next(lead for lead in leads if "Berlin" in (lead.raw_text or ""))
        assert (berlin.country, berlin.language) == ("DE", "de")
        evidence_count = (await s.execute(select(func.count()).select_from(Evidence))).scalar_one()
        assert evidence_count == 8
        source = await s.get(Source, "mock_demo")
        assert source is not None
        assert (source.last_status, source.leads_found_total) == ("ok", 8)


async def test_rerunning_a_source_never_duplicates() -> None:
    first = await _run_demo()
    async with session_scope() as s:
        row = await s.get(Source, "mock_demo")
        assert row is not None
        row.config = {**row.config, "_state": {"counter": 0}}  # replay the same items
    async with session_scope() as s:
        again = await run_source(s, get_adapters(), get_settings(), "mock_demo")
    assert again.lead_ids == []
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Lead))).scalar_one() == len(first)


async def test_disabled_source_and_missing_key_do_not_crash() -> None:
    async with session_scope() as s:
        await ensure_source_rows(s, get_settings())
        res = await run_source(s, get_adapters(), get_settings(), "reddit")
        assert res.status == "disabled"
        forced = await run_source(s, get_adapters(), get_settings(), "reddit", force=True)
        assert forced.status == "needs_api_key"
        row = await s.get(Source, "reddit")
        assert row is not None
        assert row.last_status == "needs_api_key"


class _FlakyAgent:
    name: ClassVar[str] = "requirements"
    expensive: ClassVar[bool] = False
    calls = 0

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        type(self).calls += 1
        if type(self).calls == 1:
            raise RuntimeError("boom")
        return AgentOutcome(proceed=False, status="qualified", reason="test stop")


async def test_pipeline_records_failure_then_resumes_and_skips_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lead_ids = await _run_demo()
    monkeypatch.setattr(agent_registry, "load_agents", lambda: {"requirements": _FlakyAgent()})
    monkeypatch.setattr(pipeline, "load_agents", lambda: {"requirements": _FlakyAgent()})
    with pytest.raises(RuntimeError, match="boom"):
        await pipeline.run_lead(lead_ids[0])
    state = await pipeline.run_lead(lead_ids[0])
    assert state["stopped_at"] == "requirements"
    await pipeline.run_lead(lead_ids[0])  # already done → skipped, no new run
    async with session_scope() as s:
        runs = (await s.execute(select(AgentRun).order_by(AgentRun.created_at))).scalars().all()
        assert [r.status for r in runs] == [RunStatus.FAILED, RunStatus.SUCCEEDED]
        assert runs[1].attempts == 2
        lead = await s.get(Lead, lead_ids[0])
        assert lead is not None
        assert lead.status == "qualified"
    assert _FlakyAgent.calls == 2


async def test_api_endpoints() -> None:
    from app.main import create_app  # noqa: PLC0415

    lead_ids = await _run_demo()
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/health")).json()["database"] is True
        overview = (await client.get("/api/overview")).json()
        assert overview["stages"][0]["key"] == "discovery"
        assert overview["stages"][0]["current"] == 8
        page = (await client.get("/api/leads", params={"country": "GB", "page_size": 5})).json()
        assert page["total"] >= 1
        assert all(item["country"] == "GB" for item in page["items"])
        assert all(item["is_demo"] for item in page["items"])
        detail = (await client.get(f"/api/leads/{lead_ids[0]}")).json()
        assert detail["evidence"]
        assert (await client.get(f"/api/leads/{uuid.uuid4()}")).status_code == 404
        sources = (await client.get("/api/sources")).json()
        assert {s["key"] for s in sources} >= {"search_api", "rss", "reddit", "mock_demo"}
        bad = await client.patch("/api/sources/rss", json={"config": {"_state": {}}})
        assert bad.status_code == 422
        bad_url = await client.patch(
            "/api/sources/rss", json={"config": {"feeds": [{"url": "ftp://x"}]}}
        )
        assert bad_url.status_code == 422
        ok = await client.patch(
            "/api/sources/rss",
            json={
                "config": {"feeds": [{"url": "https://example.com/feed"}]},
                "schedule_minutes": 60,
            },
        )
        assert ok.status_code == 200
        forbidden = await client.patch("/api/settings", json={"changes": {"autonomy_stage": 3}})
        assert forbidden.status_code == 403
        saved = await client.patch("/api/settings", json={"changes": {"daily_send_limit": 5}})
        assert saved.json()["runtime"]["daily_send_limit"] == 5
        hits = (await client.get("/api/search", params={"q": "Riverside"})).json()
        assert hits[0]["title"] == "Riverside Plumbing Co"
        assert (await client.get("/api/files/../../etc/passwd")).status_code in (400, 404)
        activity = (await client.get("/api/activity")).json()
        assert any(a["kind"] == "lead_discovered" for a in activity)
