"""Postgres cache for free-tier results.

Identical PageSpeed runs and deterministic LLM calls are served from the cache instead of
spending quota again; creative calls (logo concepts) are never cached.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiCache


def make_key(provider: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{provider}:{hashlib.sha256(raw).hexdigest()[:64]}"[:80]


async def get(session: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await session.get(ApiCache, key)
    if row is None or row.expires_at <= datetime.now(UTC):
        return None
    row.hits = (row.hits or 0) + 1
    return row.value


async def put(
    session: AsyncSession, key: str, provider: str, value: dict[str, Any], *, ttl: timedelta
) -> None:
    expires = datetime.now(UTC) + ttl
    stmt = insert(ApiCache).values(key=key, provider=provider, value=value, expires_at=expires)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ApiCache.key], set_={"value": value, "expires_at": expires}
    )
    async with session.begin_nested():
        await session.execute(stmt)


async def purge_expired(session: AsyncSession) -> int:
    result = await session.execute(delete(ApiCache).where(ApiCache.expires_at <= datetime.now(UTC)))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def stats(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(ApiCache.provider, ApiCache.hits))).all()
    out: dict[str, dict[str, int]] = {}
    for provider, hits in rows:
        p = out.setdefault(provider, {"entries": 0, "hits": 0})
        p["entries"] += 1
        p["hits"] += int(hits or 0)
    return out
