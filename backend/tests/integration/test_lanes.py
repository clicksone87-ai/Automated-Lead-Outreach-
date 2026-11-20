"""Fast lane: batches, parallel steps, time limits, deferral and the sweep."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from sqlalchemy import select

from app.adapters.llm import MockLLM
from app.adapters.registry import get_adapters
from app.agents.discovery.runner import ensure_source_rows, run_source
from app.agents.requirements.batch import BatchClassification
from app.config import get_settings
from app.db.models import AgentRun, Lead, LeadStatus, RunStatus, Source
from app.db.session import session_scope
from app.orchestrator import lanes
from app.orchestrator.agents import AgentContext, AgentOutcome
from app.orchestrator.pipeline import StepTimeoutError, build_graph, execute_step
from app.orchestrator.quota import FreeQuotaExceededError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _demo_leads(n: int = 8) -> list[uuid.UUID]:
    async with session_scope() as s:
        await ensure_source_rows(s, get_settings())
        row = await s.get(Source, "mock_demo")
        assert row is not None
        row.config = {"per_run": n}
    async with session_scope() as s:
        result = await run_source(s, get_adapters(), get_settings(), "mock_demo")
    return result.lead_ids or []


async def test_fast_batch_scores_in_one_call_and_records_lane_timings() -> None:
    ids = await _demo_leads()
    llm = get_adapters().llm
    assert isinstance(llm, MockLLM)
    before = len(llm.calls)
    out = await lanes.run_fast_batch(ids)
    assert out["leads"] == len(ids)
    calls = llm.calls[before:]
    batch_calls = [c for c in calls if c[1] is BatchClassification]
    assert len(batch_calls) == 1  # every surviving explicit request scored in one call
    single_scoring = [
        c for c in calls if c[0].agent == "requirements" and c[1] is not BatchClassification
    ]
    assert single_scoring == []
    async with session_scope() as s:
        leads = (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars().all()
        runs = (await s.execute(select(AgentRun.agent, AgentRun.lead_id))).all()
    assert all((lead.lane_timings.get("fast") or {}).get("seconds") is not None for lead in leads)
    assert {lead_id for agent, lead_id in runs if agent == "prefilter"} == set(ids)
    assert any(lead.status == LeadStatus.AWAITING_APPROVAL for lead in leads)  # drafted
    assert all(lead.status != LeadStatus.DISCOVERED for lead in leads)  # nothing left hanging


async def test_scoring_cap_defers_the_batch_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _demo_leads(4)

    async def capped(*args: object, **kwargs: object) -> None:
        raise FreeQuotaExceededError("gemini", 500, 500, 1)

    monkeypatch.setattr(lanes, "classify_batch", capped)
    await lanes.run_fast_batch(ids)
    async with session_scope() as s:
        leads = (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars().all()
        due_now = await lanes.due_for_fast_lane(s)
    deferred = [lead for lead in leads if (lead.lane_timings.get("fast") or {}).get("deferred")]
    assert deferred, "explicit requests wait for the cap to reset"
    for lead in deferred:
        assert lead.status == LeadStatus.DISCOVERED
        retry = datetime.fromisoformat(lead.lane_timings["fast"]["retry_after"])
        assert retry - datetime.now(UTC) > timedelta(minutes=50)
        assert lead.id not in due_now


def test_is_due() -> None:
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    old = now - timedelta(hours=2)

    def iso(dt: datetime) -> str:
        return dt.isoformat()

    assert lanes.is_due("discovered", {}, old, now)  # never queued
    assert not lanes.is_due("discovered", {"fast": {"queued": iso(now)}}, old, now)
    assert lanes.is_due("discovered", {"fast": {"queued": iso(old)}}, old, now)  # job lost
    assert not lanes.is_due(
        "qualified", {"fast": {"started": iso(now - timedelta(minutes=5))}}, old, now
    )
    assert lanes.is_due("qualified", {"fast": {"started": iso(old)}}, old, now)  # worker died
    assert not lanes.is_due("audited", {"fast": {"finished": iso(old)}}, old, now)
    assert lanes.is_due(
        "audited", {"fast": {"retry_after": iso(now - timedelta(minutes=1))}}, old, now
    )
    assert not lanes.is_due(
        "audited", {"fast": {"retry_after": iso(now + timedelta(minutes=1))}}, old, now
    )
    assert lanes.is_due("paused_budget", {"fast": {"finished": iso(old)}}, old, now)
    assert not lanes.is_due("paused_budget", {}, now - timedelta(minutes=5), now)


class _Slow:
    name: ClassVar[str] = "contact_enrichment"
    expensive: ClassVar[bool] = False

    def __init__(self, seconds: float, proceed: bool = True) -> None:
        self.seconds, self.proceed = seconds, proceed

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        await asyncio.sleep(self.seconds)
        return AgentOutcome(proceed=self.proceed, reason="done")


async def _plain_lead() -> uuid.UUID:
    async with session_scope() as s:
        lead = Lead(source="rss", source_url="https://x.test/1", source_item_id=uuid.uuid4().hex,
                    trigger_type="explicit_request", pipeline_state={})  # fmt: skip
        s.add(lead)
        await s.flush()
        return lead.id


async def test_step_time_limit_is_recorded_as_retrying() -> None:
    lead_id = await _plain_lead()
    with pytest.raises(StepTimeoutError):
        await execute_step(_Slow(5), lead_id, get_adapters(), get_settings(), timeout_s=0.2)
    async with session_scope() as s:
        run = (await s.execute(select(AgentRun))).scalars().one()
    assert run.status == RunStatus.RETRYING and "timed out" in str(run.error)


async def test_parallel_group_runs_side_by_side_and_stops_on_either() -> None:
    lead_id = await _plain_lead()

    class A(_Slow):
        name: ClassVar[str] = "audit"

    class B(_Slow):
        name: ClassVar[str] = "branding"

    class C(_Slow):
        name: ClassVar[str] = "website_builder"

    agents = {"audit": A(0.6), "branding": B(0.6, proceed=False), "website_builder": C(0)}
    graph = build_graph(agents, get_adapters(), steps=[("audit", "branding"), "website_builder"])
    t0 = time.monotonic()
    state = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    assert time.monotonic() - t0 < 1.1  # 0.6 s each, run together
    assert state["completed"] == ["audit", "branding"]
    assert state["stopped_at"] == "branding"


async def test_enqueue_fast_marks_leads_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator import queue  # noqa: PLC0415

    batches: list[list[uuid.UUID]] = []

    async def fake(lead_ids: list[uuid.UUID]) -> bool:
        batches.append(lead_ids)
        return True

    monkeypatch.setattr(queue, "enqueue_fast_batch", fake)
    ids = [await _plain_lead() for _ in range(5)]
    assert await lanes.enqueue_fast(ids, batch_size=2) == 3
    assert [len(b) for b in batches] == [2, 2, 1]
    async with session_scope() as s:
        leads = (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars().all()
    assert all(lead.lane_timings["fast"]["queued"] for lead in leads)


async def test_continue_lead_after_approval_runs_outreach() -> None:
    ids = await _demo_leads()
    await lanes.run_fast_batch(ids)
    from app.db.models import Proposal  # noqa: PLC0415

    async with session_scope() as s:
        proposal = (
            await s.execute(select(Proposal).where(Proposal.to_email.is_not(None)).limit(1))
        ).scalar_one_or_none()
        if proposal is None:
            pytest.skip("demo data produced no email lead")
        proposal.approval_status, proposal.approved_by = "approved", "test"
        lead_id = proposal.lead_id
    state = await lanes.continue_lead(lead_id)
    # Approval lets the lead continue to Outreach, where the policy gate decides: countries may
    # send worldwide now, so the next check (no sender identity in the test settings) blocks it.
    assert state["completed"][-1] == "outreach"
    assert state.get("reason", "").startswith("policy blocked sending: sender name")
    async with session_scope() as s:
        outreach_runs = (
            (
                await s.execute(
                    select(AgentRun).where(
                        AgentRun.agent == "outreach", AgentRun.lead_id == lead_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert outreach_runs


async def test_retried_batch_keeps_the_first_timings() -> None:
    ids = await _demo_leads(4)
    await lanes.run_fast_batch(ids)
    async with session_scope() as s:
        before = {
            lead.id: dict(lead.lane_timings["fast"])
            for lead in (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars()
        }
    out = await lanes.run_fast_batch(ids)  # e.g. arq retrying the job after a worker restart
    assert out["leads"] == 0
    async with session_scope() as s:
        after = {
            lead.id: dict(lead.lane_timings["fast"])
            for lead in (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars()
        }
    assert after == before


def test_needs_fast() -> None:
    assert lanes.needs_fast("discovered", {})
    assert not lanes.needs_fast("awaiting_approval", {"fast": {"finished": "x"}})
    assert lanes.needs_fast("audited", {"fast": {"finished": "x", "retry_after": "y"}})
    assert lanes.needs_fast("paused_budget", {"fast": {"finished": "x"}})
