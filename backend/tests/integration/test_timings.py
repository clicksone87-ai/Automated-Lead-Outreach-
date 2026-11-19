"""Timing read model: per-step percentiles, lane timings and first step → draft."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db.models import AgentRun, Lead, RunStatus
from app.db.session import session_scope
from app.services.timings import timings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _lead(s: object, **kw: object) -> Lead:
    lead = Lead(source="rss", source_url="https://x.test/1", source_item_id=uuid.uuid4().hex,
                trigger_type="explicit_request", pipeline_state={}, **kw)  # fmt: skip
    s.add(lead)  # type: ignore[attr-defined]
    await s.flush()  # type: ignore[attr-defined]
    return lead


async def test_step_and_lane_timings() -> None:
    t0 = datetime.now(UTC) - timedelta(hours=1)
    async with session_scope() as s:
        lead = await _lead(s, lane_timings={"fast": {"seconds": 90, "stopped_at": "proposal"}})
        await _lead(s, lane_timings={"fast": {"seconds": 0.3, "stopped_at": "prefilter"}})
        await _lead(
            s,
            lane_timings={
                "fast": {"seconds": 150, "stopped_at": "proposal"},
                "heavy": {"seconds": 420, "stopped_at": "proposal"},
            },
        )
        for agent, secs in (("requirements", 2), ("contact_enrichment", 4), ("proposal", 30)):
            s.add(AgentRun(agent=agent, lead_id=lead.id, status=RunStatus.SUCCEEDED,
                           started_at=t0, finished_at=t0 + timedelta(seconds=secs)))  # fmt: skip
            t0 += timedelta(seconds=secs)
        # failed runs are not timed
        s.add(AgentRun(agent="audit", lead_id=lead.id, status=RunStatus.FAILED,
                       started_at=t0, finished_at=t0 + timedelta(seconds=999)))  # fmt: skip
    async with session_scope() as s:
        out = await timings(s, days=7)
    steps = {x.agent: x for x in out.steps}
    assert set(steps) == {"requirements", "contact_enrichment", "proposal"}
    assert steps["proposal"].p50_s == 30
    lanes = {x.lane: x for x in out.lanes}
    assert lanes["fast"].runs == 3  # the prefilter stop below is counted, not timed
    assert lanes["fast"].leads == 2 and lanes["fast"].p50_s == 120
    assert lanes["fast"].within_target and lanes["fast"].target_s == 180
    assert lanes["heavy"].leads == 1 and lanes["heavy"].within_target
    assert out.to_proposal_leads == 1 and out.to_proposal_p50_s == 36


async def test_timings_endpoint() -> None:
    from app.main import create_app  # noqa: PLC0415

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/timings", params={"days": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["window_days"] == 3 and body["steps"] == []
        assert [lane["lane"] for lane in body["lanes"]] == ["fast", "heavy"]
        assert (await client.get("/api/timings", params={"days": 0})).status_code == 422
