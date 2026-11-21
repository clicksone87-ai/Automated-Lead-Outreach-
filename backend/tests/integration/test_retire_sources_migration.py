"""Data migration dd5dd9cbf259: retired sources off and unschedulable, provider and Scout leads
in the social family, Scout's untouched default config covering every platform."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.agents.discovery.sources import SOURCES
from app.db.models import Lead, Source
from app.db.session import session_scope

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

ROOT = Path(__file__).resolve().parents[2]
RETIRED = ("social_x", "social_reddit", "freelancer", "alert_emails")


def _alembic(action: str, target: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    getattr(command, action)(cfg, target)


def _lead(source: str, family: str) -> Lead:
    return Lead(source=source, source_family=family, source_url="https://example.com/x",
                source_item_id=uuid.uuid4().hex, trigger_type="explicit_request",
                pipeline_state={})  # fmt: skip


async def test_retired_sources_disabled_and_families_backfilled() -> None:
    old_scout = {"platforms": ["tiktok", "youtube", "instagram", "linktree", "pinterest"],
                 "queries_per_run": 1, "max_profiles": 8}  # fmt: skip
    async with session_scope() as s:
        s.add_all(Source(key=k, enabled=True, schedule_minutes=30, config={}) for k in RETIRED)
        s.add(Source(key="scout_profiles", enabled=True, schedule_minutes=360, config=old_scout))
        s.add_all([_lead("social_providers", "other"), _lead("scout_profiles", "other"),
                   _lead("search_api", "google")])  # fmt: skip
    await asyncio.to_thread(_alembic, "downgrade", "8d1c4e7a2b90")
    await asyncio.to_thread(_alembic, "upgrade", "head")
    async with session_scope() as s:
        for key in RETIRED:
            row = await s.get(Source, key)
            assert row is not None and not row.enabled, key
            assert key not in SOURCES  # never scheduled or listed again
        scout = await s.get(Source, "scout_profiles")
        assert scout is not None
        assert "twitch" in scout.config["platforms"] and "github" in scout.config["platforms"]
        assert scout.config["queries_per_run"] == 2 and scout.config["max_profiles"] == 8
        families = {
            lead.source: lead.source_family
            for lead in (await s.execute(Lead.__table__.select())).all()  # type: ignore[attr-defined]
        }
    assert families == {
        "social_providers": "social", "scout_profiles": "social", "search_api": "google"
    }  # fmt: skip
