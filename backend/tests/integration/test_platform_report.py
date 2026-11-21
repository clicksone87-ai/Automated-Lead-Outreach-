"""Leads-per-platform report (V2 Social sources)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Brief, Lead, Source, SourceRun
from app.db.session import session_scope
from app.services.platforms import platform_report

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _lead(source: str, score: int | None, *, age: timedelta = timedelta(hours=1)) -> Lead:
    return Lead(
        source=source,
        source_url=f"https://example.com/{uuid.uuid4().hex}",
        source_item_id=uuid.uuid4().hex,
        trigger_type="explicit_request",
        intent_score=score,
        pipeline_state={},
        created_at=datetime.now(UTC) - age,
    )


async def test_platform_report_counts_leads_runs_and_sellers() -> None:
    async with session_scope() as s:
        s.add_all(
            [
                Source(key="social_x", enabled=True, schedule_minutes=360, config={}),
                Source(key="social_reddit", enabled=False, schedule_minutes=360, config={}),
            ]
        )
        await s.flush()
        x_high, x_low, seller = _lead("social_x", 80), _lead("social_x", 50), _lead("x", 20)
        old = _lead("social_x", 90, age=timedelta(days=10))  # outside the window
        reddit = _lead("social_reddit", 65, age=timedelta(days=2))
        s.add_all([x_high, x_low, seller, old, reddit])
        await s.flush()
        s.add(Brief(lead_id=seller.id, data={"intent": {"signals": {"seller_not_buyer": -45}}}))
        s.add(Brief(lead_id=x_high.id, data={"intent": {"signals": {"budget_mentioned": 10}}}))
        s.add_all(
            [
                SourceRun(
                    source_key="social_x", status="succeeded", items_seen=20, leads_created=3
                ),
                SourceRun(source_key="social_x", status="failed", items_seen=0),
            ]
        )

    async with session_scope() as s:
        report = await platform_report(s, days=7)
    by = {p.platform: p for p in report.platforms}
    assert report.recent_hours == 8
    assert list(by) == [
        "x", "reddit", "instagram", "facebook", "tiktok", "threads", "youtube", "linkedin"
    ]  # fmt: skip
    x = by["x"]
    assert (x.leads, x.leads_recent, x.qualified, x.high, x.sellers_filtered) == (3, 3, 1, 1, 1)
    assert (x.items_seen, x.runs, x.failed_runs, x.enabled) == (20, 2, 1, True)
    assert len(x.daily) == 7 and x.daily[-1] == 3
    r = by["reddit"]
    assert (r.leads, r.leads_recent, r.qualified, r.enabled) == (1, 0, 1, False)
    assert r.daily[-3] == 1
    assert by["instagram"].leads == 0 and by["instagram"].daily == [0] * 7


async def test_provider_leads_report_under_their_platform() -> None:
    async with session_scope() as s:
        s.add(Source(key="social_providers", enabled=True, schedule_minutes=60, config={}))
        tiktok = _lead("social_providers", 70)
        tiktok.source_item_id = "tiktok:71"
        x = _lead("social_providers", 40)
        x.source_item_id = "x:2069"
        linkedin = _lead("social_providers", 75, age=timedelta(hours=9))
        linkedin.source_item_id = "linkedin:7529686502218502145"
        s.add_all([tiktok, x, linkedin, _lead("social_x", 80)])
    async with session_scope() as s:
        by = {p.platform: p for p in (await platform_report(s, days=7)).platforms}
    assert (by["tiktok"].leads, by["tiktok"].qualified, by["tiktok"].enabled) == (1, 1, True)
    assert by["tiktok"].sources == ["social_providers"]
    assert by["x"].leads == 2 and by["threads"].leads == 0
    li = by["linkedin"]
    assert (li.label, li.leads, li.leads_recent, li.high) == ("LinkedIn", 1, 0, 1)  # 9 h > 8 h
