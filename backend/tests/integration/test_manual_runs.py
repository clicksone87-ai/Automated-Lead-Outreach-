"""Manual "Run now" guard: one run per hour for limited sources, never past a free-tier cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.registry import get_adapters
from app.config import Settings
from app.db.models import Cost, PolicyDecision
from app.db.session import session_scope
from app.orchestrator import quota
from app.orchestrator.quota import FreeQuotaExceededError
from app.services import manual_runs

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


def test_run_needs_by_source() -> None:
    assert manual_runs.run_needs("rss", {}) == []
    assert manual_runs.run_needs("companies_house", {}) == []
    x = manual_runs.run_needs("social_x", {"max_charge_usd": 0.02})
    assert [(n.scope, n.amount) for n in x] == [("apify", 0.02), ("apify:social_x", 0.02)]
    fb = manual_runs.run_needs(
        "social_facebook", {"max_charge_usd": 0.05, "public_group_urls": ["g"]}
    )
    assert fb[0].amount == pytest.approx(0.1)  # search + groups actor
    serper = manual_runs.run_needs("search_api", {"queries_per_run": 3})
    assert {(n.scope, n.amount) for n in serper} == {("serper", 3), ("serper:daily", 3)}
    assert manual_runs.run_needs("no_website_check", {"places_lookups": False}) == []


async def test_free_sources_are_not_limited() -> None:
    async with session_scope() as s:
        v = await manual_runs.authorize(s, _settings(), get_adapters(), "rss")
        assert v.allowed and not v.limited
        logged = (await s.execute(select(PolicyDecision))).scalars().all()
        assert logged == []  # free sources are not rate limited or logged


async def test_one_manual_run_per_hour() -> None:
    settings = _settings()
    async with session_scope() as s:
        first = await manual_runs.authorize(s, settings, get_adapters(), "search_api")
        assert first.allowed and first.limited
        assert any("serper" in line for line in first.usage)
    async with session_scope() as s:
        second = await manual_runs.authorize(s, settings, get_adapters(), "search_api")
        assert not second.allowed
        assert second.status_code == 429
        assert second.next_allowed_at is not None
        # another limited source has its own hour
        other = await manual_runs.authorize(s, settings, get_adapters(), "local_sites")
        assert other.allowed
    async with session_scope() as s:
        later = datetime.now(UTC) + timedelta(hours=1, minutes=1)
        v = await manual_runs.evaluate(s, settings, get_adapters(), "search_api", now=later)
        assert v.allowed


async def test_refused_when_free_cap_cannot_cover_a_run() -> None:
    settings = _settings(serper_daily_credit_cap=4)
    async with session_scope() as s:
        await quota.consume(s, "serper", agent="discovery", units=2, settings=settings)
    async with session_scope() as s:
        v = await manual_runs.authorize(s, settings, get_adapters(), "search_api")
        assert not v.allowed
        assert v.status_code == 409
        assert v.reason and "serper:daily" in v.reason
        refusal = (await s.execute(select(PolicyDecision))).scalars().one()
        assert refusal.allowed is False and refusal.check == "manual_run"


async def test_serper_daily_ceiling_applies_to_every_call() -> None:
    settings = _settings(serper_daily_credit_cap=2)
    async with session_scope() as s:
        await quota.consume(s, "serper", agent="t", settings=settings)
        await quota.consume(s, "serper", agent="t", settings=settings)
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(s, "serper", agent="t", settings=settings)
        rows = (await s.execute(select(Cost))).scalars().all()
        assert len(rows) == 2
        rep = {r["scope"]: r for r in await quota.report(s, settings)}
        assert rep["serper:daily"]["used"] == 2
        assert rep["serper"]["used"] == 2


async def test_run_now_endpoint_enforces_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import routes  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    queued: list[str] = []

    async def fake_enqueue(key: str, slot: str, *, force: bool = False) -> bool:
        queued.append(key)
        return True

    monkeypatch.setattr(routes, "enqueue_source", fake_enqueue)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/sources/nope/run")).status_code == 404
        assert (await client.post("/api/sources/search_api/run")).status_code == 202
        again = await client.post("/api/sources/search_api/run")
        assert again.status_code == 429
        assert "one manual run per hour" in again.json()["detail"].lower()
        assert (await client.post("/api/sources/rss/run")).status_code == 202
        assert (await client.post("/api/sources/rss/run")).status_code == 202
        sources = {s["key"]: s for s in (await client.get("/api/sources")).json()}
        assert sources["search_api"]["manual_run"]["limited"] is True
        assert sources["search_api"]["manual_run"]["allowed"] is False
        assert sources["rss"]["manual_run"]["limited"] is False
    assert queued == ["search_api", "rss", "rss"]
    async with session_scope() as s:
        decisions = (
            (await s.execute(select(PolicyDecision).order_by(PolicyDecision.id))).scalars().all()
        )
        assert [d.allowed for d in decisions] == [True, False]
