"""Routing: every reachable lead with a known need gets drafts (Track A email through the
Approval queue, Track B manual drafts with the person's steps), the rest is "Not useful"."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.registry import get_adapters
from app.agents.proposal.agent import ProposalAgent
from app.config import get_settings
from app.db.models import (
    AgentRun,
    ApprovalStatus,
    Brief,
    Business,
    Contact,
    Lead,
    LeadStatus,
    ManualOutreach,
    ManualOutreachStatus,
    Proposal,
    RunStatus,
)
from app.db.session import session_scope
from app.orchestrator import lanes, runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

NOW = datetime.now(UTC)


async def _lead(
    *,
    channel: str = "email",
    status: str = LeadStatus.CONTACT_FOUND,
    services: list[str] | None = None,
    timings: dict[str, Any] | None = None,
    contact: bool = True,
    score: int = 50,
    domain: str | None = None,
) -> uuid.UUID:
    """A lead whose fast lane ended before its draft (here: on an error)."""
    services = ["website", "logo"] if services is None else services
    async with session_scope() as s:
        biz = Business(name=f"Biz {uuid.uuid4().hex[:6]}", normalized_name=uuid.uuid4().hex,
                       country="US", domain=domain, has_website=bool(domain))  # fmt: skip
        s.add(biz)
        await s.flush()
        state: dict[str, Any] = {}
        if contact:
            row = Contact(business_id=biz.id, type=channel,
                          value="hi@biz.example.com" if channel == "email" else "https://x.com/biz/status/1",
                          source_url="https://biz.example.com/",
                          verification_status="valid" if channel == "email" else "not_applicable")  # fmt: skip
            s.add(row)
            await s.flush()
            state = {"contact_id": str(row.id), "contact_channel": channel}
        lead = Lead(business_id=biz.id, source="social_x", source_url="https://x.com/biz/status/1",
                    source_item_id=uuid.uuid4().hex, trigger_type="explicit_request", country="US",
                    language="en", intent_score=score, status=status, service_needed=services,
                    title="Need a website and a logo for my bakery", lane="fast",
                    lane_timings=timings if timings is not None else
                    {"fast": {"finished": (NOW - timedelta(hours=3)).isoformat(), "stopped_at": "error"}},
                    pipeline_state=state)  # fmt: skip
        s.add(lead)
        await s.flush()
        s.add(Brief(lead_id=lead.id, data={"services": services, "agents_to_run": [
            "contact_enrichment", "branding", "website_builder", "quality_check", "proposal", "outreach"]}))  # fmt: skip
        return lead.id


async def _draft(lead_id: uuid.UUID) -> None:
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=get_adapters(), settings=get_settings())
        await runs.execute(ctx, ProposalAgent(), lead_id)


async def _proposals(lead_id: uuid.UUID) -> list[Proposal]:
    async with session_scope() as s:
        rows = await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))
        return list(rows.scalars())


# ── which leads the drafts sweep picks up ───────────────────────────────────


async def test_drafts_sweep_picks_every_reachable_lead_without_a_draft() -> None:
    track_a = await _lead(score=40)  # low score: not a top lead, still gets its draft
    track_b = await _lead(channel="platform_reply", score=65)
    old = await _lead(score=30)
    async with session_scope() as s:  # older than the fast sweep's 7-day window
        lead = await s.get(Lead, old)
        assert lead is not None
        lead.created_at = NOW - timedelta(days=20)
    heavy_failed = await _lead(score=90, timings={
        "fast": {"queued": (NOW - timedelta(minutes=45)).isoformat()},  # fast job was lost
        "heavy": {"queued": (NOW - timedelta(hours=1)).isoformat(),
                  "finished": NOW.isoformat(), "stopped_at": "error"}})  # fmt: skip
    # not picked:
    no_route = await _lead(contact=False)
    no_need = await _lead(services=[])
    fast_running = await _lead(
        timings={"fast": {"started": (NOW - timedelta(minutes=2)).isoformat()}}
    )
    retry_later = await _lead(
        timings={"fast": {"retry_after": (NOW + timedelta(minutes=9)).isoformat()}}
    )
    heavy_live = await _lead(timings={"fast": {"finished": NOW.isoformat()},
                                      "heavy": {"queued": (NOW - timedelta(minutes=5)).isoformat()}})  # fmt: skip
    drafted = await _lead()
    await _draft(drafted)
    tried = await _lead()
    awaiting = await _lead(status=LeadStatus.AWAITING_APPROVAL)
    gone = await _lead(status=LeadStatus.NOT_USEFUL)
    async with session_scope() as s:
        s.add(AgentRun(agent="proposal", lead_id=tried, status=RunStatus.FAILED, attempts=1,
                       started_at=NOW - timedelta(minutes=20)))  # fmt: skip
    async with session_scope() as s:
        due = await lanes.due_for_drafts(s)
    assert due[0] == heavy_failed  # highest intent first
    assert set(due) == {track_a, track_b, old, heavy_failed}
    for skipped in (no_route, no_need, fast_running, retry_later, heavy_live, drafted, tried,
                    awaiting, gone):  # fmt: skip
        assert skipped not in due


async def test_drafts_sweep_gives_up_after_repeated_failures() -> None:
    lead_id = await _lead()
    async with session_scope() as s:
        for hours in range(2, 2 + lanes.DRAFT_MAX_ATTEMPTS):
            s.add(AgentRun(agent="proposal", lead_id=lead_id, status=RunStatus.FAILED, attempts=1,
                           started_at=NOW - timedelta(hours=hours)))  # fmt: skip
    async with session_scope() as s:
        assert await lanes.due_for_drafts(s) == []


async def test_scheduler_queues_the_drafts_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.orchestrator import queue, scheduler  # noqa: PLC0415

    queued: list[uuid.UUID] = []

    async def fake(lead_id: uuid.UUID) -> bool:
        queued.append(lead_id)
        return True

    monkeypatch.setattr(queue, "enqueue_pipeline", fake)
    lead_id = await _lead()
    assert await scheduler.draft_sweep({}) == 1
    assert queued == [lead_id]
    assert any(
        job.coroutine is scheduler.draft_sweep for job in scheduler.SchedulerSettings.cron_jobs
    )


# ── Track A: email → Approval queue, never auto-approved at stage 1 ─────────


async def test_track_a_draft_waits_in_the_approval_queue() -> None:
    from app.main import create_app  # noqa: PLC0415

    lead_id = await _lead(score=40)
    await _draft(lead_id)
    [proposal] = await _proposals(lead_id)
    assert proposal.to_email == "hi@biz.example.com"
    assert proposal.approval_status == ApprovalStatus.PENDING and proposal.approved_by is None
    assert "track_b" not in (proposal.draft or {})
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        approvals = (await client.get("/api/approvals")).json()
        assert [a["lead_id"] for a in approvals] == [str(lead_id)]
        assert (await client.get("/api/manual-outreach")).json() == []


# ── Track B: manual drafts and the person's steps ───────────────────────────


async def _queue_item(lead_id: uuid.UUID) -> uuid.UUID:
    async with session_scope() as s:
        item = (
            await s.execute(select(ManualOutreach).where(ManualOutreach.lead_id == lead_id))
        ).scalar_one()
        return item.id


async def test_track_b_steps_confirm_prepare_assets_and_mark_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import create_app  # noqa: PLC0415
    from app.orchestrator import queue  # noqa: PLC0415

    heavy_jobs: list[uuid.UUID] = []

    async def fake_heavy(lead_id: uuid.UUID) -> bool:
        heavy_jobs.append(lead_id)
        return True

    monkeypatch.setattr(queue, "enqueue_heavy", fake_heavy)
    lead_id = await _lead(channel="platform_reply", services=["logo"])
    await _draft(lead_id)
    [proposal] = await _proposals(lead_id)
    assert proposal.to_email is None and proposal.draft["track_b"]["dm"]
    item_id = await _queue_item(lead_id)
    base = f"/api/manual-outreach/{item_id}"

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/approvals")).json() == []  # Track B is not approved there
        [item] = (await client.get("/api/manual-outreach", params={"state": "pending"})).json()
        assert item["step"] == "confirm_contact" and item["assets_suggested"] == ["logo"]
        assert item["dm"] and item["assets_progress"] is None

        early = await client.post(f"{base}/prepare-assets", json={"logo": True})
        assert early.status_code == 409 and "confirm" in early.json()["detail"]
        confirmed = (await client.post(f"{base}/confirm-contact", json={"done_by": "omar"})).json()
        assert confirmed["step"] == "prepare_assets" and confirmed["contact_confirmed_by"] == "omar"
        nothing = await client.post(f"{base}/prepare-assets", json={})
        assert nothing.status_code == 409

        prepared = await client.post(
            f"{base}/prepare-assets", json={"logo": True, "done_by": "omar"}
        )
        assert prepared.status_code == 202
        body = prepared.json()
        assert body["queued"] is True and heavy_jobs == [lead_id]
        assert body["step"] == "preparing_assets"
        assert body["assets_requested"]["kinds"] == ["logo"]
        assert [s["step"] for s in body["assets_progress"]["steps"]] == [
            "branding", "quality_check", "proposal"]  # fmt: skip
        assert all(s["state"] == "waiting" for s in body["assets_progress"]["steps"])
        busy = await client.post(f"{base}/prepare-assets", json={"logo": True})
        assert busy.status_code == 409  # already being generated

        async with session_scope() as s:  # exactly the chosen asset is built
            brief = await s.get(Brief, lead_id)
            assert brief is not None
            assert brief.data["agents_to_run"] == [
                "contact_enrichment", "branding", "quality_check", "proposal", "outreach"]  # fmt: skip

        # the heavy worker finishes: logos made, message redrafted with them
        async with session_scope() as s:
            s.add(AgentRun(agent="branding", lead_id=lead_id, status=RunStatus.SUCCEEDED,
                           attempts=1, started_at=datetime.now(UTC), finished_at=datetime.now(UTC)))  # fmt: skip
        await lanes.mark([lead_id], "heavy", started=datetime.now(UTC).isoformat())
        await lanes.mark([lead_id], "heavy", finished=datetime.now(UTC).isoformat(),
                         stopped_at="proposal")  # fmt: skip
        [item] = (await client.get("/api/manual-outreach")).json()
        assert item["step"] == "ready_to_send"
        states = {s["step"]: s["state"] for s in item["assets_progress"]["steps"]}
        assert states["branding"] == "done"

        sent = await client.post(f"{base}/mark-sent", json={"done_by": "omar"})
        assert sent.status_code == 200 and sent.json()["step"] == "sent"
        assert (await client.post(f"{base}/mark-sent", json={})).status_code == 409
        assert (await client.get("/api/manual-outreach")).json() == []
        [done] = (await client.get("/api/manual-outreach", params={"state": "done"})).json()
        assert done["sent_by"] == "omar"
        assert (await client.post(f"/api/manual-outreach/{uuid.uuid4()}/mark-sent",
                                  json={})).status_code == 404  # fmt: skip

    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None and lead.status == LeadStatus.SENT
        business = await s.get(Business, lead.business_id)
        assert business is not None and business.cooldown_until is not None
        assert business.cooldown_until > datetime.now(UTC) + timedelta(days=1)


async def test_track_b_contact_that_does_not_work_makes_the_lead_not_useful() -> None:
    from app.main import create_app  # noqa: PLC0415

    lead_id = await _lead(channel="contact_form")
    await _draft(lead_id)
    item_id = await _queue_item(lead_id)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        failed = await client.post(f"/api/manual-outreach/{item_id}/contact-failed", json={})
        assert failed.status_code == 200 and failed.json()["status"] == ManualOutreachStatus.SKIPPED
        assert (await client.get("/api/manual-outreach")).json() == []
        tab = (await client.get("/api/not-useful", params={"reason": "no_contact"})).json()
        assert [row["id"] for row in tab["items"]] == [str(lead_id)]
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None and lead.status == LeadStatus.NOT_USEFUL
        with pytest.raises(lanes.AssetsRequestError):
            await lanes.request_assets(s, lead_id, kinds=["logo"])


async def test_website_choice_on_a_site_adds_the_audit_and_drops_the_logo() -> None:
    lead_id = await _lead(channel="platform_reply", domain="biz.example.com")
    async with session_scope() as s:
        await lanes.request_assets(s, lead_id, kinds=["website"])
        brief = await s.get(Brief, lead_id)
        assert brief is not None
        assert brief.data["agents_to_run"] == [
            "contact_enrichment", "audit", "website_builder", "quality_check", "proposal", "outreach"]  # fmt: skip
    assert lanes.suggested_kinds(["seo"]) == ["website"]
    assert lanes.suggested_kinds(["logo", "redesign"]) == ["logo", "website"]


async def test_assets_asked_again_redraft_the_message_and_rerun_a_skipped_producer() -> None:
    lead_id = await _lead(channel="platform_reply")
    async with session_scope() as s:
        s.add(Proposal(lead_id=lead_id, version=1, language="en", subject="s", body_html="h",
                       body_text="t", approval_status=ApprovalStatus.PENDING,
                       attachments=[{"kind": "preview_desktop", "path": "p.png", "filename": "p.png"}]))  # fmt: skip
        s.add(AgentRun(agent="branding", lead_id=lead_id, status=RunStatus.SUCCEEDED, attempts=1,
                       started_at=NOW - timedelta(hours=2), finished_at=NOW - timedelta(hours=2),
                       output={"proceed": True, "reason": "branding not needed for this lead"}))  # fmt: skip
    async with session_scope() as s:  # assets already attached, nothing new asked for
        assert not await lanes._proposal_needs_assets(s, lead_id)
        brief = await s.get(Brief, lead_id)
        assert brief is not None
        brief.data = {**brief.data, "agents_to_run": ["proposal"]}
    async with session_scope() as s:
        assert not await lanes.HEAVY_FORCE["branding"](s, lead_id)  # still not wanted
        s.add(ManualOutreach(lead_id=lead_id, channel="platform_reply", drafts={},
                             assets_requested={"kinds": ["logo"], "steps": ["branding"],
                                               "at": (datetime.now(UTC) + timedelta(seconds=5)).isoformat()}))  # fmt: skip
        brief = await s.get(Brief, lead_id)
        assert brief is not None
        brief.data = {**brief.data, "agents_to_run": ["branding", "quality_check", "proposal"]}
    async with session_scope() as s:
        assert await lanes._proposal_needs_assets(s, lead_id)
        assert await lanes.HEAVY_FORCE["branding"](s, lead_id)
