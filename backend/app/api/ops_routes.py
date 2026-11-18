"""Monitoring and weekly-report endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep
from app.db.models import AgentRun, Report, RunStatus, Source
from app.learning.report import build_report, run_weekly
from app.orchestrator import quota
from app.services import cache

router = APIRouter(prefix="/api")
STALE_AFTER = timedelta(minutes=5)


async def _heartbeats(redis_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        client = Redis.from_url(redis_url, socket_timeout=2, decode_responses=True)
        for name in ("worker", "worker-heavy", "scheduler"):
            raw = await client.get(f"heartbeat:{name}")
            at = datetime.fromisoformat(raw) if raw else None
            out[name] = {"last": raw, "alive": bool(at and datetime.now(UTC) - at < STALE_AFTER)}
        out["queue_depth"] = int(await client.zcard("arq:queue"))
        out["heavy_queue_depth"] = int(await client.zcard("arq:heavy"))
        await client.aclose()
    except Exception as exc:  # monitoring must never fail the page
        out["error"] = str(exc)[:200]
    return out


@router.get("/monitoring")
async def monitoring(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=24)
    failures = (
        await session.execute(
            select(AgentRun.agent, func.count())
            .where(AgentRun.status == RunStatus.FAILED, AgentRun.started_at >= since)
            .group_by(AgentRun.agent)
        )
    ).all()
    recent = (
        (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.status == RunStatus.FAILED, AgentRun.started_at >= since)
                .order_by(AgentRun.started_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    unhealthy = (
        (
            await session.execute(
                select(Source).where(
                    Source.enabled.is_(True), Source.last_status.in_(["error", "degraded"])
                )
            )
        )
        .scalars()
        .all()
    )
    quotas = [
        q
        for q in await quota.report(session, settings)
        if q["cap"] and float(str(q["used"])) / float(str(q["cap"])) >= 0.8
    ]
    hb = await _heartbeats(settings.redis_url)
    problems = (
        len(failures)
        + len(unhealthy)
        + len(quotas)
        + sum(1 for k in ("worker", "scheduler") if not (hb.get(k) or {}).get("alive"))
    )
    return {
        "status": "ok" if problems == 0 else "attention",
        "heartbeats": hb,
        "agent_failures_24h": {agent: int(n) for agent, n in failures},
        "recent_failures": [
            {
                "agent": r.agent,
                "lead_id": str(r.lead_id) if r.lead_id else None,
                "at": r.started_at.isoformat() if r.started_at else None,
                "error": (r.error or "")[-300:],
            }
            for r in recent
        ],
        "sources_unhealthy": [
            {"key": s.key, "status": s.last_status, "error": (s.last_error or "")[:200]}
            for s in unhealthy
        ],
        "quotas_over_80pct": quotas,
        "cache": await cache.stats(session),
    }


@router.get("/reports")
async def reports(session: SessionDep, limit: int = 12) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Report).order_by(Report.period_end.desc()).limit(min(limit, 52))
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(r.id), "kind": r.kind, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
             "data": r.data, "adjustments": r.adjustments, "created_at": r.created_at.isoformat()} for r in rows]  # fmt: skip


@router.get("/reports/live")
async def live_report(session: SessionDep, days: int = 7) -> dict[str, Any]:
    return await build_report(session, days=max(1, min(days, 90)))


@router.post("/reports/run")
async def run_report(session: SessionDep) -> dict[str, Any]:
    r = await run_weekly(session)
    return {"id": str(r.id), "adjustments": r.adjustments, "data": r.data}
