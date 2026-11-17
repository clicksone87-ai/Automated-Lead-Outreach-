"""Arq worker: runs per-lead pipelines and discovery sources.

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
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator import pipeline
from app.orchestrator.queue import DEFAULT_QUEUE, enqueue_pipeline, redis_settings
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
    try:
        state = await pipeline.run_lead(uuid.UUID(lead_id), get_adapters())
    except Exception as exc:
        if _is_transient(exc) and ctx["job_try"] < get_settings().max_job_attempts:
            raise Retry(defer=backoff_seconds(ctx["job_try"])) from exc
        raise
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


async def shutdown(ctx: dict[str, Any]) -> None:
    await get_adapters().fetcher.aclose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_pipeline, run_source_job]
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute=set(range(60)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    queue_name = DEFAULT_QUEUE
    max_jobs = 4  # Chromium/Lighthouse are heavy; keeps the worker within its 2 GB limit
    # One pipeline job can include 3 logo concepts + a website build (~15 min on a laptop).
    job_timeout = 3600
    max_tries = get_settings().max_job_attempts
    keep_result = 3600
