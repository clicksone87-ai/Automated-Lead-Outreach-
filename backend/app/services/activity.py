"""Activity feed — streamed to the dashboard over SSE."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActivityEvent


async def emit(
    session: AsyncSession,
    kind: str,
    message: str,
    *,
    lead_id: uuid.UUID | None = None,
    agent: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    session.add(
        ActivityEvent(
            kind=kind, message=message[:2000], lead_id=lead_id, agent=agent, data=data or {}
        )
    )
    await session.flush()
