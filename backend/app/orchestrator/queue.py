"""Redis job queue helpers (Arq)."""

from __future__ import annotations

import uuid

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from app.config import get_settings

DEFAULT_QUEUE = "arq:queue"
SCHEDULER_QUEUE = "arq:scheduler"

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    """Tolerate short Redis stalls (e.g. during website builds) instead of crashing."""
    rs = RedisSettings.from_dsn(get_settings().redis_url)
    rs.conn_timeout = 15
    rs.conn_retries = 10
    rs.conn_retry_delay = 2
    return rs


async def get_pool() -> ArqRedis:
    global _pool  # noqa: PLW0603 - process-wide connection pool
    if _pool is None:
        _pool = await create_pool(redis_settings(), default_queue_name=DEFAULT_QUEUE)
    return _pool


async def enqueue_pipeline(lead_id: uuid.UUID) -> bool:
    """Queue the per-lead pipeline. Deduplicated: one queued job per lead at a time."""
    pool = await get_pool()
    job = await pool.enqueue_job("run_pipeline", str(lead_id), _job_id=f"pipeline:{lead_id}")
    return job is not None


async def enqueue_source(key: str, slot: str, *, force: bool = False) -> bool:
    pool = await get_pool()
    job = await pool.enqueue_job("run_source_job", key, force, _job_id=f"source:{key}:{slot}")
    return job is not None
