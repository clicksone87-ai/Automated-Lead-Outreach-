"""How long leads take: per pipeline step, per lane and end to end (read model for the dashboard).

Step times come from `agent_runs`; lane times from the timings each lane writes on the lead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRun, Lead, RunStatus
from app.schemas import LaneTiming, StepTiming, TimingsOut

FAST_TARGET_S = 180
COMPLETE_STOPS = ("proposal", "outreach")  # drafted (waiting for approval) or sent
HEAVY_TARGET_S = 600


def _seconds(start: Any, end: Any) -> Any:
    return func.extract("epoch", end - start)


def _pct(col: Any, p: float) -> Any:
    return func.percentile_cont(p).within_group(col)


async def timings(session: AsyncSession, *, days: int = 7) -> TimingsOut:
    since = datetime.now(UTC) - timedelta(days=days)
    secs = _seconds(AgentRun.started_at, AgentRun.finished_at)
    step_rows = (
        await session.execute(
            select(
                AgentRun.agent,
                func.count(),
                _pct(secs, 0.5),
                _pct(secs, 0.9),
                func.avg(secs),
                func.max(secs),
            )
            .where(
                AgentRun.status == RunStatus.SUCCEEDED,
                AgentRun.finished_at.is_not(None),
                AgentRun.started_at >= since,
            )
            .group_by(AgentRun.agent)
        )
    ).all()
    steps = [
        StepTiming(
            agent=agent,
            runs=int(n),
            p50_s=round(float(p50), 1),
            p90_s=round(float(p90), 1),
            avg_s=round(float(avg), 1),
            max_s=round(float(mx), 1),
        )
        for agent, n, p50, p90, avg, mx in step_rows
    ]

    lanes: list[LaneTiming] = []
    for lane, target in (("fast", FAST_TARGET_S), ("heavy", HEAVY_TARGET_S)):
        entry = Lead.lane_timings[lane]
        lane_secs = cast(entry["seconds"].astext, Float)
        timed = and_(entry["seconds"].is_not(None), Lead.updated_at >= since)
        # The targets are for runs that went all the way to a draft; runs that stopped early
        # (prefilter, no contact, a free-tier cap) would make the lane look faster than it is.
        complete = entry["stopped_at"].astext.in_(COMPLETE_STOPS)
        total = int((await session.execute(select(func.count()).where(timed))).scalar_one())
        row = (
            await session.execute(
                select(func.count(), _pct(lane_secs, 0.5), _pct(lane_secs, 0.9)).where(
                    timed, complete
                )
            )
        ).one()
        n, p50, p90 = int(row[0]), row[1], row[2]
        lanes.append(
            LaneTiming(
                lane=lane,
                leads=n,
                runs=total,
                p50_s=round(float(p50), 1) if p50 is not None else None,
                p90_s=round(float(p90), 1) if p90 is not None else None,
                target_s=target,
                within_target=bool(n) and p90 is not None and float(p90) <= target,
            )
        )

    # End to end: first step start → proposal drafted, for leads that got a proposal.
    first = (
        select(AgentRun.lead_id, func.min(AgentRun.started_at).label("t0"))
        .where(AgentRun.started_at >= since)
        .group_by(AgentRun.lead_id)
        .subquery()
    )
    drafted = (
        select(AgentRun.lead_id, func.min(AgentRun.finished_at).label("t1"))
        .where(
            AgentRun.agent == "proposal",
            AgentRun.status == RunStatus.SUCCEEDED,
            AgentRun.started_at >= since,
        )
        .group_by(AgentRun.lead_id)
        .subquery()
    )
    e2e = _seconds(first.c.t0, drafted.c.t1)
    row = (
        await session.execute(
            select(func.count(), _pct(e2e, 0.5), _pct(e2e, 0.9)).select_from(
                first.join(drafted, and_(first.c.lead_id == drafted.c.lead_id))
            )
        )
    ).one()
    return TimingsOut(
        window_days=days,
        steps=sorted(steps, key=lambda s: -s.p50_s),
        lanes=lanes,
        to_proposal_leads=int(row[0]),
        to_proposal_p50_s=round(float(row[1]), 1) if row[1] is not None else None,
        to_proposal_p90_s=round(float(row[2]), 1) if row[2] is not None else None,
    )
