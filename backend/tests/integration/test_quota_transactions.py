"""Free-tier usage is recorded in its own short transaction."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Cost, PolicyDecision
from app.db.session import session_scope
from app.orchestrator import quota
from app.orchestrator.quota import FreeQuotaExceededError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


async def _failing_step() -> None:
    async with session_scope() as s:
        await quota.consume(s, "gemini", agent="proposal")
        raise RuntimeError("the step failed after the API call")


async def test_usage_survives_a_failed_step() -> None:
    with pytest.raises(RuntimeError):
        await _failing_step()
    async with session_scope() as s:
        assert len((await s.execute(select(Cost))).scalars().all()) == 1


async def test_open_transaction_does_not_block_other_workers() -> None:
    async with session_scope() as a:
        await quota.consume(a, "gemini", agent="w1")
        # `a` is still open; before, its advisory lock blocked everyone else until it committed
        async with session_scope() as b:
            await asyncio.wait_for(quota.consume(b, "gemini", agent="w2"), timeout=10)


async def test_refusal_is_kept_and_unknown_lead_link_is_dropped() -> None:
    settings = _settings(pagespeed_daily_cap=1)
    ghost = uuid.uuid4()  # a lead that is not committed (or does not exist)
    async with session_scope() as s:
        row = await quota.consume(s, "pagespeed", agent="audit", lead_id=ghost, settings=settings)
        assert row.lead_id is None and row.id
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(s, "pagespeed", agent="audit", settings=settings)
    async with session_scope() as s:
        refusal = (await s.execute(select(PolicyDecision))).scalars().one()
        assert refusal.check == "free_quota" and not refusal.allowed


async def test_settle_updates_the_reservation() -> None:
    async with session_scope() as s:
        reserved = await quota.consume(s, "exa", agent="d", credit_usd=0.01)
        await quota.settle(s, reserved, units=1, credit_usd=0.004, description="settled")
    async with session_scope() as s:
        row = (await s.execute(select(Cost))).scalars().one()
        assert row.credit_usd == pytest.approx(0.004) and row.description == "settled"
