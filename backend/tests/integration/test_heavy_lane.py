"""Heavy lane: daily top-N pick, "Generate assets", and the proposal redrafted with assets."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import select

from app.adapters.registry import get_adapters
from app.agents.proposal.agent import ProposalAgent
from app.db.models import (
    AgentRun,
    ApprovalStatus,
    Brief,
    Business,
    Contact,
    Lead,
    LeadStatus,
    Proposal,
    RunStatus,
)
from app.db.session import session_scope
from app.orchestrator import lanes
from app.orchestrator.agents import AgentContext, AgentOutcome
from app.orchestrator.pipeline import build_graph
from app.services import runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

ASSETS = [
    "contact_enrichment",
    "branding",
    "website_builder",
    "quality_check",
    "proposal",
    "outreach",
]


async def _ready_lead(
    *,
    score: int = 80,
    channel: str = "email",
    agents: list[str] | None = None,
    fast_done: bool = True,
) -> uuid.UUID:
    now = datetime.now(UTC)
    async with session_scope() as s:
        biz = Business(
            name=f"Biz {uuid.uuid4().hex[:6]}", normalized_name=uuid.uuid4().hex, country="US"
        )
        s.add(biz)
        await s.flush()
        contact = Contact(business_id=biz.id, type=channel, value="hi@biz.example.com" if channel == "email"
                          else "https://biz.example.com/contact", source_url="https://biz.example.com/",
                          verification_status="valid" if channel == "email" else "not_applicable")  # fmt: skip
        s.add(contact)
        await s.flush()
        timings: dict[str, Any] = (
            {"fast": {"finished": now.isoformat(), "seconds": 60}} if fast_done else {}
        )
        lead = Lead(business_id=biz.id, source="rss", source_url="https://forum.test/1",
                    source_item_id=uuid.uuid4().hex, trigger_type="explicit_request", country="US",
                    language="en", intent_score=score, status=LeadStatus.AWAITING_APPROVAL, lane="fast",
                    lane_timings=timings,
                    pipeline_state={"contact_id": str(contact.id), "contact_channel": channel})  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(
            Brief(
                lead_id=lead.id,
                data={"agents_to_run": agents or ASSETS, "services": ["website", "logo"]},
            )
        )
        return lead.id


async def test_pick_prefers_email_then_intent_within_the_daily_limit() -> None:
    form_high = await _ready_lead(score=95, channel="contact_form")
    email_mid = await _ready_lead(score=75)
    email_high = await _ready_lead(score=90)
    await _ready_lead(score=50)  # below the minimum intent
    await _ready_lead(agents=["contact_enrichment", "proposal", "outreach"])  # nothing heavy to do
    await _ready_lead(fast_done=False)  # fast lane not finished
    async with session_scope() as s:
        await runtime_settings.update(s, {"heavy_lane_daily_limit": 2}, changed_by="test")
    async with session_scope() as s:
        picked = await lanes.pick_for_heavy(s)
    assert picked == [email_high, email_mid]
    assert form_high not in picked


async def test_daily_limit_counts_leads_already_sent_today(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator import queue  # noqa: PLC0415

    async def fake_enqueue(lead_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue, "enqueue_heavy", fake_enqueue)
    first = await _ready_lead(score=90)
    second = await _ready_lead(score=85)
    async with session_scope() as s:
        await runtime_settings.update(s, {"heavy_lane_daily_limit": 1}, changed_by="test")
    assert await lanes.send_to_heavy([first], requested_by="daily_pick") == 1
    async with session_scope() as s:
        lead = await s.get(Lead, first)
        assert lead is not None and lead.lane == "heavy" and lead.lane_timings["heavy"]["queued"]
        assert await lanes.pick_for_heavy(s) == []  # today's slot is used
    assert second


async def test_generate_assets_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app  # noqa: PLC0415
    from app.orchestrator import queue  # noqa: PLC0415

    sent: list[uuid.UUID] = []

    async def fake_enqueue(lead_id: uuid.UUID) -> bool:
        sent.append(lead_id)
        return True

    monkeypatch.setattr(queue, "enqueue_heavy", fake_enqueue)
    ok = await _ready_lead(score=40)  # a person may pick any reachable lead
    seo_only = await _ready_lead(agents=["contact_enrichment", "proposal"])
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post(f"/api/leads/{uuid.uuid4()}/assets")).status_code == 404
        assert (await client.post(f"/api/leads/{ok}/assets")).json() == {"queued": True}
        again = await client.post(f"/api/leads/{ok}/assets")
        assert again.status_code == 409 and "already" in again.json()["detail"]
        nothing = await client.post(f"/api/leads/{seo_only}/assets")
        assert nothing.status_code == 409
    assert sent == [ok]


class _Done:
    expensive: ClassVar[bool] = False

    def __init__(self, name: str) -> None:
        self.name = name  # type: ignore[misc]

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        return AgentOutcome(proceed=True, reason="ok")


async def test_heavy_lane_redrafts_a_pending_proposal_and_supersedes_the_old_one() -> None:
    lead_id = await _ready_lead()
    agents: dict[str, Any] = {
        n: _Done(n) for n in ("audit", "branding", "website_builder", "quality_check")
    }
    agents["proposal"] = ProposalAgent()
    # the fast lane already drafted v1 (waiting for approval)
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=get_adapters(), settings=get_settings_())
        from app.orchestrator import runs  # noqa: PLC0415

        await runs.execute(ctx, agents["proposal"], lead_id)
    graph = build_graph(
        agents, get_adapters(), steps=lanes.HEAVY_STEPS, force_hooks=lanes.HEAVY_FORCE
    )
    state = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    assert state["stopped_at"] == "proposal"  # waits for a human again, now with v2
    async with session_scope() as s:
        proposals = (
            (
                await s.execute(
                    select(Proposal).where(Proposal.lead_id == lead_id).order_by(Proposal.version)
                )
            )
            .scalars()
            .all()
        )
        runs_ = (
            (
                await s.execute(
                    select(AgentRun).where(
                        AgentRun.agent == "proposal", AgentRun.lead_id == lead_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [p.version for p in proposals] == [1, 2]
    assert proposals[0].approval_status == ApprovalStatus.SUPERSEDED
    assert proposals[1].approval_status == ApprovalStatus.PENDING
    assert sum(r.status == RunStatus.SUCCEEDED for r in runs_) == 2


async def test_no_redraft_once_a_proposal_is_approved() -> None:
    lead_id = await _ready_lead()
    async with session_scope() as s:
        s.add(Proposal(lead_id=lead_id, version=1, language="en", subject="s", body_html="h", body_text="t",
                       approval_status=ApprovalStatus.APPROVED))  # fmt: skip
    async with session_scope() as s:
        assert not await lanes._proposal_needs_assets(s, lead_id)


def get_settings_() -> Any:
    from app.config import get_settings  # noqa: PLC0415

    return get_settings()


async def test_fast_finished_timing_window() -> None:
    old = await _ready_lead()
    async with session_scope() as s:
        lead = await s.get(Lead, old)
        assert lead is not None
        lead.lane_timings = {
            "fast": {"finished": (datetime.now(UTC) - timedelta(days=2)).isoformat()}
        }
    async with session_scope() as s:
        assert await lanes.pick_for_heavy(s) == []  # only leads from the last 24 h


async def test_concurrent_competitor_lookups_share_one_search() -> None:
    import asyncio  # noqa: PLC0415

    from app.adapters.search import SearchResult  # noqa: PLC0415
    from app.services.competitors import competitor_domains  # noqa: PLC0415

    class CountingSearch:
        is_mock = False
        calls = 0

        async def search(self, session: object, query: str, **kw: object) -> list[SearchResult]:
            type(self).calls += 1
            await asyncio.sleep(0.2)
            return [
                SearchResult(title="t", url="https://rival-plumbing.example.com/", description="s")
            ]

    biz = Business(name="Acme", normalized_name="acme", domain="acme.example.com", country="US")
    brief = {"business_type": "plumber", "location": "Austin, TX"}

    async def one() -> list[str]:
        async with session_scope() as s:
            return await competitor_domains(s, CountingSearch(), biz, brief, agent="audit")  # type: ignore[arg-type]

    a, b = await asyncio.gather(one(), one())
    assert a == b == ["rival-plumbing.example.com"]
    assert CountingSearch.calls == 1


def test_heavy_in_progress_expires() -> None:
    now = datetime.now(UTC)
    recent = {"queued": (now - timedelta(minutes=5)).isoformat()}
    assert lanes.heavy_in_progress(recent, now)
    assert not lanes.heavy_in_progress({**recent, "finished": now.isoformat()}, now)
    stale = {
        "queued": (now - timedelta(hours=3)).isoformat(),
        "started": (now - timedelta(hours=3)).isoformat(),
    }
    assert not lanes.heavy_in_progress(stale, now)  # its worker died: a new request may start


async def test_failed_heavy_run_is_recorded_not_left_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.orchestrator import pipeline  # noqa: PLC0415

    lead_id = await _ready_lead()

    async def broken(*args: object, **kwargs: object) -> None:
        raise PermissionError("cache not writable")

    monkeypatch.setattr(pipeline, "run_steps", broken)
    with pytest.raises(PermissionError):
        await lanes.run_heavy(lead_id)
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        heavy = lead.lane_timings["heavy"]
    assert heavy["stopped_at"] == "error" and "not writable" in heavy["error"]
    assert not lanes.heavy_in_progress(heavy)


async def test_no_heavy_picks_while_gemini_daily_limit_is_used_up() -> None:
    lead_id = await _ready_lead(score=90)
    now = datetime.now(UTC)
    async with session_scope() as s:
        s.add(AgentRun(agent="branding", lead_id=lead_id, status=RunStatus.SKIPPED,
                       error="budget cap reached for gemini:daily(server): spent $0 + $1 > cap $0",
                       started_at=now, finished_at=now))  # fmt: skip
    async with session_scope() as s:
        assert await lanes.gemini_daily_limit_hit(s)
        assert await lanes.pick_for_heavy(s) == []
    # the next Pacific day starts fresh
    tomorrow = lanes.gemini_day_start(now) + timedelta(days=1, hours=1)
    async with session_scope() as s:
        assert not await lanes.gemini_daily_limit_hit(s, tomorrow)
