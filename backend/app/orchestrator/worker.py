"""Arq worker: fast, discovery and heavy queues in one process.

Retries: transient failures are retried with exponential backoff up to MAX_JOB_ATTEMPTS.
Expected stops (budget cap, missing API key, policy) are recorded and not retried.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from typing import Any, ClassVar

import httpx
from arq import Retry, Worker, cron

from app.adapters.registry import get_adapters
from app.agents.discovery.runner import run_source
from app.config import get_settings, log_missing_integrations
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator import lanes, pipeline
from app.orchestrator.queue import DEFAULT_QUEUE, DISCOVERY_QUEUE, HEAVY_QUEUE, redis_settings
from app.orchestrator.runs import TransientAgentError

logger = logging.getLogger(__name__)

TRANSIENT = (TransientAgentError, httpx.TransportError, httpx.TimeoutException)


def backoff_seconds(job_try: int, base: int = 15, cap: int = 600) -> int:
    """15s, 30s, 60s, 120s … capped at 10 min (longer waits belong to the sweep)."""
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


async def run_fast_batch_job(ctx: dict[str, Any], lead_ids: list[str]) -> dict[str, Any]:
    return await lanes.run_fast_batch([uuid.UUID(x) for x in lead_ids], get_adapters())


async def run_heavy_job(ctx: dict[str, Any], lead_id: str) -> dict[str, Any]:
    state = await lanes.run_heavy(uuid.UUID(lead_id), get_adapters())
    return {"completed": state.get("completed"), "stopped_at": state.get("stopped_at")}


async def run_source_job(ctx: dict[str, Any], key: str, force: bool = False) -> dict[str, Any]:
    async with session_scope() as session:
        result = await run_source(session, get_adapters(), get_settings(), key, force=force)
    await lanes.enqueue_fast(result.lead_ids or [])
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
    """Fast queue: fast-lane batches and resumes. Network-bound work, so several jobs run side
    by side."""

    functions: ClassVar[list[Any]] = [run_pipeline, run_fast_batch_job]
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute=set(range(60)), run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    queue_name = DEFAULT_QUEUE
    max_jobs = 4
    job_timeout = 1800
    max_tries = get_settings().max_job_attempts
    keep_result = 3600


class DiscoveryWorkerSettings:
    """Discovery queue: source runs (crawls with polite per-site delays can take minutes)."""

    functions: ClassVar[list[Any]] = [run_source_job]
    redis_settings = redis_settings()
    queue_name = DISCOVERY_QUEUE
    max_jobs = 3
    job_timeout = 1800
    max_tries = 1
    keep_result = 3600


class HeavyWorkerSettings:
    """Heavy queue: Chromium, Lighthouse and logo rendering are memory-heavy, one job at a time."""

    functions: ClassVar[list[Any]] = [run_heavy_job]
    redis_settings = redis_settings()
    queue_name = HEAVY_QUEUE
    max_jobs = 1
    job_timeout = 1800
    max_tries = 2
    keep_result = 3600


def _worker(settings: type, **overrides: Any) -> Worker:
    names = ("functions", "cron_jobs", "on_startup", "on_shutdown", "redis_settings",
             "queue_name", "max_jobs", "job_timeout", "max_tries", "keep_result")  # fmt: skip
    kwargs = {n: getattr(settings, n) for n in names if hasattr(settings, n)}
    return Worker(**{**kwargs, **overrides, "handle_signals": False})


async def run_workers() -> None:
    """All queues in one process (one Python interpreter's memory, not three)."""
    workers = [
        _worker(WorkerSettings),
        _worker(DiscoveryWorkerSettings),
        _worker(HeavyWorkerSettings),
    ]
    loop = asyncio.get_running_loop()

    def stop(sig: signal.Signals) -> None:
        for w in workers:
            w.handle_sig(sig)

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows dev hosts
            loop.add_signal_handler(sig, stop, sig)
    try:
        await asyncio.gather(*(w.async_run() for w in workers))
    except asyncio.CancelledError:
        logger.info("worker stopped")
    finally:
        await asyncio.gather(*(w.close() for w in workers), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run_workers())
