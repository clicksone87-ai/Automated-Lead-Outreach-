"""Scheduler service: periodic jobs.

Runs on its own Arq queue so it only executes these light cron tasks; heavy work is
enqueued onto the worker queue.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron
from sqlalchemy import select

from app.adapters.base import AdapterUnavailableError
from app.adapters.registry import get_adapters
from app.agents.discovery.runner import ensure_source_rows
from app.agents.discovery.sources.reddit import sync_deletions
from app.agents.discovery.sources.social_apify import sync_reddit_deletions
from app.config import get_settings, integration_status
from app.db.models import Source
from app.db.session import session_scope
from app.learning.report import run_weekly
from app.logging_setup import configure_logging
from app.orchestrator.queue import SCHEDULER_QUEUE, enqueue_source, redis_settings
from app.services import backup, cache, outreach_jobs

logger = logging.getLogger(__name__)


async def tick_sources(ctx: dict[str, Any]) -> int:
    """Enqueue every enabled source whose per-source schedule is due."""
    now = datetime.now(UTC)
    queued = 0
    async with session_scope() as session:
        await ensure_source_rows(session, get_settings())
        rows = (await session.execute(select(Source).where(Source.enabled.is_(True)))).scalars()
        for row in rows:
            due = (
                row.last_run_at is None
                or row.last_run_at + timedelta(minutes=row.schedule_minutes) <= now
            )
            if not due:
                continue
            slot = now.strftime("%Y%m%d%H%M")
            if await enqueue_source(row.key, slot):
                queued += 1
    return queued


async def reddit_deletion_sync(ctx: dict[str, Any]) -> int:
    """Delete stored Reddit posts that were deleted on Reddit (official API or Apify)."""
    s = get_settings()
    removed = 0
    async with session_scope() as session:
        if integration_status(s)["reddit"].configured:
            with contextlib.suppress(AdapterUnavailableError):
                removed += await sync_deletions(session, s)
        apify = get_adapters().apify
        if apify is not None:
            try:
                removed += await sync_reddit_deletions(session, apify)
            except Exception:  # an actor problem must never break the scheduler
                logger.exception("apify reddit deletion sync failed")
    return removed


async def _outreach(job: str) -> int:
    settings, adapters = get_settings(), get_adapters()
    async with session_scope() as session:
        try:
            if job == "send_due":
                return await outreach_jobs.send_due(session, settings, adapters)
            if job == "poll_delivery":
                return await outreach_jobs.poll_delivery(session, adapters)
            if job == "ingest_replies":
                return await outreach_jobs.ingest_replies(session, settings, adapters)
            if job == "follow_ups":
                return await outreach_jobs.follow_ups(session, settings, adapters)
        except AdapterUnavailableError as exc:
            logger.info("outreach job %s skipped: %s", job, exc)
    return 0


async def send_due(ctx: dict[str, Any]) -> int:
    return await _outreach("send_due")


async def poll_delivery(ctx: dict[str, Any]) -> int:
    return await _outreach("poll_delivery")


async def ingest_replies(ctx: dict[str, Any]) -> int:
    return await _outreach("ingest_replies")


async def follow_ups(ctx: dict[str, Any]) -> int:
    return await _outreach("follow_ups")


async def daily_backup(ctx: dict[str, Any]) -> str:
    return str(await backup.run_backup())


async def weekly_report(ctx: dict[str, Any]) -> str:
    async with session_scope() as session:
        report = await run_weekly(session)
        await cache.purge_expired(session)
        return str(report.id)


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Monitoring: proves the scheduler is alive (read by /api/monitoring)."""
    from datetime import UTC, datetime  # noqa: PLC0415

    redis = ctx.get("redis")
    if redis is not None:
        await redis.set("heartbeat:scheduler", datetime.now(UTC).isoformat(), ex=600)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()


class SchedulerSettings:
    functions: ClassVar[list[Any]] = []
    cron_jobs: ClassVar[list[Any]] = [
        cron(tick_sources, minute=set(range(60)), run_at_startup=True),
        cron(reddit_deletion_sync, hour={4}, minute={7}),
        cron(daily_backup, hour={3}, minute={15}),
        cron(send_due, minute=set(range(0, 60, 5))),
        cron(ingest_replies, minute=set(range(2, 60, 5))),
        cron(poll_delivery, minute={11, 26, 41, 56}),
        cron(follow_ups, minute={33}),
        cron(weekly_report, weekday={0}, hour={6}, minute={30}),
        cron(heartbeat, minute=set(range(60))),
    ]
    on_startup = startup
    redis_settings = redis_settings()
    queue_name = SCHEDULER_QUEUE
    max_jobs = 2
