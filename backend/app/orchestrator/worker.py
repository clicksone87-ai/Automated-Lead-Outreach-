"""Arq workers: the fast worker runs discovery and every lead's fast lane; the heavy worker
runs audits, logos and website builds for top leads (see `lanes`).

Retries: transient failures are retried with exponential backoff up to MAX_JOB_ATTEMPTS.
Expected stops (budget cap, missing API key, policy) are recorded and not retried.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

import httpx
from arq import Retry, cron

from app.adapters.registry import get_adapters
from app.agents.discovery.runner import run_source
from app.config import get_settings, log_missing_integrations
from app.db.models import Brief, Lead
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator import lanes, pipeline
from app.orchestrator.queue import (
    DEFAULT_QUEUE,
    HEAVY_QUEUE,
    enqueue_heavy,
    enqueue_pipeline,
    redis_settings,
)
from app.orchestrator.runs import TransientAgentError

logger = logging.getLogger(__name__)

TRANSIENT = (TransientAgentError, httpx.TransportError, httpx.TimeoutException)


def backoff_seconds(job_try: int, base: int = 15, cap: int = 1800) -> int:
    """15s, 30s, 60s, 120s … capped at 30 min."""
    return int(min(cap, base * 2 ** max(0, job_try - 1)))


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code >= 500 or exc.response.status_code == 429
    )


async def run_pipeline(ctx: dict[str, Any], lead_id: str) -> dict[str, Any]:
    """Fast lane, then hand top leads to the heavy worker."""
    lid = uuid.UUID(lead_id)
    try:
        state = await pipeline.run_lead(lid, get_adapters(), lane="fast")
    except Exception as exc:
        if _is_transient(exc) and ctx["job_try"] < get_settings().max_job_attempts:
            raise Retry(defer=backoff_seconds(ctx["job_try"])) from exc
        raise
    heavy = await maybe_queue_heavy(lid)
    return {
        "completed": state.get("completed"),
        "stopped_at": state.get("stopped_at"),
        "heavy_queued": heavy,
    }


async def maybe_queue_heavy(lead_id: uuid.UUID) -> bool:
    """Queue the heavy lane when the lead qualifies and it is not already queued/done."""
    async with session_scope() as session:
        lead = await session.get(Lead, lead_id)
        brief = await session.get(Brief, lead_id)
        if lead is None or brief is None or not lanes.qualifies(lead, brief.data):
            return False
        if lanes.is_active(lead) or not await lanes.outstanding(session, lead, brief.data):
            return False
        lanes.set_state(lead, "queued", f"intent {lead.intent_score}")
    return await enqueue_heavy(lead_id)


async def run_heavy(ctx: dict[str, Any], lead_id: str) -> dict[str, Any]:
    lid = uuid.UUID(lead_id)
    async with session_scope() as session:
        lead = await session.get(Lead, lid)
        if lead is None:
            return {"skipped": "lead not found"}
        lanes.set_state(lead, "running")
    try:
        state = await pipeline.run_lead(lid, get_adapters(), lane="heavy")
    except Exception as exc:
        if _is_transient(exc) and ctx["job_try"] < get_settings().max_job_attempts:
            raise Retry(defer=backoff_seconds(ctx["job_try"])) from exc
        async with session_scope() as session:
            if (lead := await session.get(Lead, lid)) is not None:
                lanes.set_state(lead, "failed", str(exc)[:300])
        raise
    async with session_scope() as session:
        if (lead := await session.get(Lead, lid)) is not None:
            stopped = state.get("stopped_at")
            # Stopping at the proposal/outreach (approval wait) means the heavy work is done.
            done = stopped in (None, "proposal", "outreach")
            lanes.set_state(lead, "done" if done else "stopped", state.get("reason", ""))
    return {"completed": state.get("completed"), "stopped_at": state.get("stopped_at")}


async def run_source_job(ctx: dict[str, Any], key: str, force: bool = False) -> dict[str, Any]:
    async with session_scope() as session:
        result = await run_source(session, get_adapters(), get_settings(), key, force=force)
    for lead_id in result.lead_ids or []:
        await enqueue_pipeline(lead_id)
    return {
        "source": key,
        "status": result.status,
        "items": result.items_seen,
        "leads": len(result.lead_ids or []),
    }


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    log_missing_integrations(get_settings())


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Monitoring: proves the worker is alive and consuming (read by /api/monitoring)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    await ctx["redis"].set("heartbeat:worker", datetime.now(UTC).isoformat(), ex=600)


async def heavy_heartbeat(ctx: dict[str, Any]) -> None:
    """Own function name: arq cron job ids are global, so sharing `heartbeat` would collide."""
    from datetime import UTC, datetime  # noqa: PLC0415

    await ctx["redis"].set("heartbeat:worker-heavy", datetime.now(UTC).isoformat(), ex=600)


async def shutdown(ctx: dict[str, Any]) -> None:
    await get_adapters().fetcher.aclose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_pipeline, run_source_job]
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute=set(range(60)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    queue_name = DEFAULT_QUEUE
    max_jobs = 4  # fast lane only: LLM calls and page fetches, no builds
    job_timeout = 600
    max_tries = get_settings().max_job_attempts
    keep_result = 3600


class HeavyWorkerSettings:
    functions: ClassVar[list[Any]] = [run_heavy]
    cron_jobs: ClassVar[list[Any]] = [
        cron(heavy_heartbeat, minute=set(range(60)), run_at_startup=True)
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    queue_name = HEAVY_QUEUE
    max_jobs = 1  # Chromium, Lighthouse and next build are memory-heavy
    # One heavy job can include an audit, three logo concepts and a website build.
    job_timeout = 3600
    max_tries = get_settings().max_job_attempts
    keep_result = 3600
