"""Scheduler service: periodic jobs.

Runs on its own Arq queue so it only executes these light cron tasks; heavy work is
enqueued onto the worker queue.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron
from sqlalchemy import select

from app.adapters.base import AdapterUnavailableError
from app.agents.discovery.runner import ensure_source_rows
from app.agents.discovery.sources.reddit import sync_deletions
from app.config import get_settings, integration_status
from app.db.models import Source
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator.queue import SCHEDULER_QUEUE, enqueue_source, redis_settings
from app.services import backup

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
    s = get_settings()
    if not integration_status(s)["reddit"].configured:
        return 0
    async with session_scope() as session:
        try:
            return await sync_deletions(session, s)
        except AdapterUnavailableError:
            return 0


async def daily_backup(ctx: dict[str, Any]) -> str:
    return str(await backup.run_backup())


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()


class SchedulerSettings:
    functions: ClassVar[list[Any]] = []
    cron_jobs: ClassVar[list[Any]] = [
        cron(tick_sources, minute=set(range(60)), run_at_startup=True),
        cron(reddit_deletion_sync, minute={7}),
        cron(daily_backup, hour={3}, minute={15}),
    ]
    on_startup = startup
    redis_settings = redis_settings()
    queue_name = SCHEDULER_QUEUE
    max_jobs = 2
