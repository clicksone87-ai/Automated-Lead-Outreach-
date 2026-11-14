"""Write operations triggered from the dashboard (validated here, not in route handlers)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.discovery.runner import ensure_source_rows
from app.agents.discovery.sources import SOURCES
from app.config import Settings
from app.db.models import Lead, Source
from app.schemas import SourcePatch
from app.services import activity


class InvalidInputError(ValueError):
    pass


def _validate_config(key: str, patch: dict[str, Any]) -> dict[str, Any]:
    defaults = SOURCES[key].default_config
    clean: dict[str, Any] = {}
    for k, raw in patch.items():
        if k.startswith("_"):
            raise InvalidInputError(f"'{k}' is internal")
        if k not in defaults:
            raise InvalidInputError(f"unknown config key '{k}' for source {key}")
        expected = type(defaults[k])
        if expected is int and isinstance(raw, bool):
            raise InvalidInputError(f"'{k}' must be an integer")
        v = expected(raw) if expected in (int, float) and isinstance(raw, int | float) else raw
        if not isinstance(v, expected):
            raise InvalidInputError(f"'{k}' must be {expected.__name__}")
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and "url" in item:
                    url = str(item["url"])
                    if not url.startswith(("http://", "https://")):
                        raise InvalidInputError(f"invalid URL: {url}")
        clean[k] = v
    return clean


async def patch_source(
    session: AsyncSession, settings: Settings, key: str, patch: SourcePatch
) -> None:
    if key not in SOURCES:
        raise KeyError(key)
    await ensure_source_rows(session, settings)
    row = await session.get(Source, key, with_for_update=True)
    assert row is not None
    if patch.enabled is not None:
        if key == "mock_demo" and patch.enabled and not settings.demo_mode:
            raise InvalidInputError("demo source can only be enabled with DEMO_MODE=true")
        row.enabled = patch.enabled
    if patch.schedule_minutes is not None:
        row.schedule_minutes = patch.schedule_minutes
    if patch.config is not None:
        clean = _validate_config(key, patch.config)
        state = (row.config or {}).get("_state", {})
        row.config = {**(row.config or {}), **clean, "_state": state}
    await activity.emit(
        session, "source_updated", f"Source {key} updated", data=patch.model_dump(exclude_none=True)
    )


async def mark_lead_for_rerun(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise KeyError(lead_id)
    await activity.emit(session, "lead_rerun", "Pipeline re-run requested", lead_id=lead_id)
    return lead
