"""Weekly world sweep on OpenStreetMap.

Every day one seventh of the countries (ISO-sorted, index % 7 == weekday) is swept: for each
of its most populous cities (GeoNames), one Overpass query finds named shops, offices, crafts
and selected amenities within OSM_SWEEP_RADIUS_M that list a phone or an email. Businesses
without a website become no-website leads through the normal ingest path and the fast lane;
businesses with a website are stored, and the site-health trigger checks their site.

Each city of each day is one `osm_sweep_cities` row. A city whose query fails stays pending
and is retried with a growing delay, up to OSM_SWEEP_MAX_ATTEMPTS, also on later days of the
week. Runs are short batches (a time budget per job), so a crash loses at most one city.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.opendata import FoundBusiness, OpenDataError, Overpass, WebsiteStatus
from app.adapters.registry import Adapters
from app.agents.discovery.base import RawBusiness, RawLead, SourceContext
from app.agents.discovery.runner import ingest
from app.config import Settings
from app.db.models import Business, EvidenceKind, GeoCity, Lead, SweepCity, TriggerType
from app.db.session import session_scope
from app.services import geonames
from app.services.business_search import store_one

logger = logging.getLogger(__name__)

CITY_PAUSE_S = (3.0, 6.0)  # between two cities, on top of OVERPASS_MAX_REQUESTS_PER_MINUTE
OVERPASS_ROUNDS = 3  # passes over the mirror list per attempt, with backoff in between
OVERPASS_TIMEOUT_S = 110  # the query asks the server for 90 s
RETRY_BASE = timedelta(minutes=20)  # 20 min, 40 min, 80 min … between attempts of a city
RETRY_CAP = timedelta(hours=6)
STALE_RUNNING = timedelta(minutes=45)  # a city "running" this long died with its worker
BACKLOG_DAYS = 6  # unfinished cities of the last days are retried after today's
JOB_BUDGET_S = 1200  # one worker job; the discovery queue's job timeout is 1800 s
STATUSES = ("pending", "running", "done", "failed")


class OsmSweepSource:
    """Lead source identity for the sweep. Not a scheduled discovery source: the sweep has its
    own scheduler job, it only reuses the ingest path."""

    key: ClassVar[str] = "osm_sweep"
    label: ClassVar[str] = "OpenStreetMap weekly sweep"
    description: ClassVar[str] = "Businesses without a website in the largest cities worldwide."
    integration: ClassVar[str] = "web"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 15
    constraints: ClassVar[str] = "Overpass usage policy: one query per city, polite pauses."
    default_config: ClassVar[dict[str, Any]] = {}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        return []


SOURCE = OsmSweepSource()


@dataclass(slots=True)
class _Claim:
    id: uuid.UUID
    day: date
    country: str
    city: str
    lat: float
    lon: float
    attempts: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


def retry_delay(attempts: int) -> timedelta:
    """Delay before the next attempt of a city, with jitter so failed cities spread out."""
    base = min(RETRY_CAP, RETRY_BASE * int(2 ** max(0, attempts - 1)))
    return base + timedelta(seconds=random.uniform(0, 300))  # noqa: S311 - jitter


def industry_for(category: str | None) -> str | None:
    """'amenity=restaurant' -> 'restaurant'; a generic 'shop=yes' -> 'shop'."""
    if not category:
        return None
    key, _, value = category.partition("=")
    return (key if value in ("", "yes") else value)[:100]


async def plan_day(session: AsyncSession, settings: Settings, day: date) -> int:
    """Create the pending rows for the day's countries (idempotent); returns rows added."""
    countries = geonames.countries_for_day(
        await geonames.rotation_countries(session, settings), day
    )
    cities = await geonames.top_cities(session, countries, settings.osm_sweep_cities_per_country)
    if not cities:
        return 0
    stmt = (
        insert(SweepCity)
        .values(
            [
                {
                    "id": uuid.uuid4(),
                    "day": day,
                    "country": c.country,
                    "geonameid": c.geonameid,
                    "city": c.name,
                    "lat": c.lat,
                    "lon": c.lon,
                    "population": c.population,
                }
                for c in cities
            ]
        )
        .on_conflict_do_nothing(constraint="uq_osm_sweep_day_city")
        .returning(SweepCity.id)
    )
    return len((await session.execute(stmt)).scalars().all())


async def claim_next(session: AsyncSession, now: datetime) -> _Claim | None:
    """Take the next city: today's first (largest first), then unfinished cities of the last
    days. Rows are locked with SKIP LOCKED so two runs never take the same city."""
    today = now.date()
    stmt = (
        select(SweepCity)
        .where(
            SweepCity.day >= today - timedelta(days=BACKLOG_DAYS),
            SweepCity.day <= today,
            or_(
                (SweepCity.status == "pending")
                & (SweepCity.next_attempt_at.is_(None) | (SweepCity.next_attempt_at <= now)),
                (SweepCity.status == "running") & (SweepCity.started_at < now - STALE_RUNNING),
            ),
        )
        .order_by(
            (SweepCity.day == today).desc(),
            SweepCity.day.desc(),
            SweepCity.population.desc().nulls_last(),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    row.status, row.started_at, row.attempts = "running", now, (row.attempts or 0) + 1
    await session.flush()
    return _Claim(row.id, row.day, row.country, row.city, row.lat, row.lon, row.attempts)


async def _fail(claim: _Claim, settings: Settings, error: str, now: datetime) -> str:
    final = claim.attempts >= settings.osm_sweep_max_attempts
    async with session_scope() as s:
        await s.execute(
            update(SweepCity)
            .where(SweepCity.id == claim.id)
            .values(
                status="failed" if final else "pending",
                error=error[:1000],
                finished_at=now,
                next_attempt_at=None if final else now + retry_delay(claim.attempts),
            )
        )
    return "failed" if final else "pending"


async def _leads_today(session: AsyncSession, now: datetime) -> int:
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    return int(
        (
            await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.source == SOURCE.key, Lead.created_at >= start
                )
            )
        ).scalar_one()
    )


async def _city_lead_cap(session: AsyncSession, settings: Settings, now: datetime) -> int:
    """An equal share of the daily lead cap per city of the day, within what is left today."""
    cap = settings.osm_sweep_daily_lead_cap
    if cap <= 0:
        return 0
    planned = (
        await session.execute(select(func.count(SweepCity.id)).where(SweepCity.day == now.date()))
    ).scalar_one()
    share = math.ceil(cap / max(1, int(planned)))
    return max(0, min(share, cap - await _leads_today(session, now)))


def _lead_for(f: FoundBusiness, b: Business, settings: Settings, city: str) -> RawLead:
    where = ", ".join(x for x in (city, b.country) if x)
    reach = " and ".join(x for x, v in (("phone", f.phone), ("email", f.email)) if v)
    return RawLead(
        source_item_id=f"osm:{f.source_id}",
        source_url=f.url or f"https://www.openstreetmap.org/{f.source_id}",
        trigger_type=TriggerType.NO_WEBSITE,
        title=f"{b.name} — no website on OpenStreetMap",
        text=(
            f"{b.name} ({industry_for(f.category) or 'business'}, {where}) is listed on "
            f"OpenStreetMap with a {reach} but without a website."
        ),
        business=RawBusiness(
            name=b.name,
            phone=f.phone,
            email=f.email,
            address=f.address,
            country_hint=b.country,
            registry_number=b.registry_number,
            registry_source=b.registry_source,
            industry=b.industry,
            has_website=False,
        ),
        evidence_kind=EvidenceKind.REGISTRY_RECORD,
        evidence_data={
            "osm_type": f.source_id.split("/")[0],
            "osm_id": f.source_id.split("/")[-1],
            "osm_url": f.url,
            "category": f.category,
            "website_status": WebsiteStatus.OSM_WEBSITE_MISSING.value,
            "tags": (f.raw or {}).get("tags", {}),
            "city": city,
            "attribution": settings.osm_attribution,
            "license": "ODbL 1.0 (https://www.openstreetmap.org/copyright)",
            "city_attribution": settings.geonames_attribution,
        },
    )


async def store_city(
    session: AsyncSession,
    settings: Settings,
    adapters: Adapters,
    claim: _Claim,
    found: list[FoundBusiness],
    *,
    now: datetime,
    mirror: str,
    elements: int,
) -> tuple[dict[str, int], list[uuid.UUID]]:
    """Upsert the city's businesses, create the no-website leads (within the daily cap) and
    mark the city done, all in one transaction."""
    counts = {
        "elements": elements,
        "found": len(found),
        "excluded": 0,
        "created": 0,
        "matched": 0,
        "no_website": 0,
        "with_website": 0,
        "leads": 0,
    }
    candidates: list[tuple[FoundBusiness, Business]] = []
    for f in found:
        stored = await store_one(session, settings, f, industry=industry_for(f.category), now=now)
        if stored is None:
            counts["excluded"] += 1
            continue
        b, created = stored
        counts["created" if created else "matched"] += 1
        if f.website_status == WebsiteStatus.OSM_WEBSITE_MISSING:
            counts["no_website"] += 1
            if not b.domain and b.has_website is not True:
                candidates.append((f, b))
        else:
            counts["with_website"] += 1
    # An email is a contact route of its own; phone-only businesses come after.
    candidates.sort(key=lambda fb: fb[0].email is None)
    cap = await _city_lead_cap(session, settings, now)
    lead_ids: list[uuid.UUID] = []
    uses_mock = adapters.uses_mock(SOURCE.integration)
    for f, b in candidates:
        if len(lead_ids) >= cap:
            break
        has_lead = (
            await session.execute(select(Lead.id).where(Lead.business_id == b.id).limit(1))
        ).scalar_one_or_none()
        if has_lead:
            continue
        lead_id = await ingest(
            session, adapters, SOURCE, _lead_for(f, b, settings, claim.city), uses_mock=uses_mock
        )
        if lead_id:
            lead_ids.append(lead_id)
    counts["leads"] = len(lead_ids)
    await session.execute(
        update(SweepCity)
        .where(SweepCity.id == claim.id)
        .values(
            status="done",
            counts=counts,
            error=None,
            mirror=mirror,
            finished_at=now,
            next_attempt_at=None,
        )
    )
    return counts, lead_ids


def make_overpass(settings: Settings) -> Overpass:
    return Overpass(
        settings.overpass_urls,
        per_minute=settings.overpass_max_requests_per_minute,
        attribution=settings.osm_attribution,
        contact=settings.overpass_contact,
        rounds=OVERPASS_ROUNDS,
        timeout_s=OVERPASS_TIMEOUT_S,
    )


async def run_batch(
    settings: Settings,
    adapters: Adapters,
    *,
    overpass: Overpass | None = None,
    budget_s: float = JOB_BUDGET_S,
    max_cities: int | None = None,
    clock: Callable[[], datetime] = _utcnow,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Sweep pending cities until none is due, the time budget is spent or `max_cities` were
    tried. Returns a summary including the new lead ids (the caller queues them)."""
    if not settings.osm_sweep_enabled:
        return {"status": "disabled", "lead_ids": []}
    async with session_scope() as s:
        planned = await plan_day(s, settings, clock().date())
    op = overpass or make_overpass(settings)
    started = time.monotonic()
    summary: dict[str, Any] = {
        "status": "ok",
        "planned": planned,
        "done": 0,
        "retry": 0,
        "failed": 0,
        "lead_ids": [],
    }
    tried = 0
    try:
        while (max_cities is None or tried < max_cities) and time.monotonic() - started < budget_s:
            async with session_scope() as s:
                claim = await claim_next(s, clock())
            if claim is None:
                break
            if tried:
                await sleep(random.uniform(*CITY_PAUSE_S))  # noqa: S311 - politeness jitter
            tried += 1
            try:
                found, mirror, elements = await op.sweep(
                    claim.lat,
                    claim.lon,
                    settings.osm_sweep_radius_m,
                    country=claim.country,
                    city=claim.city,
                )
            except OpenDataError as exc:
                outcome = await _fail(claim, settings, str(exc), clock())
                summary["failed" if outcome == "failed" else "retry"] += 1
                continue
            except Exception as exc:  # one bad answer must never stop the sweep
                logger.exception("osm sweep: %s, %s failed", claim.city, claim.country)
                outcome = await _fail(claim, settings, f"{type(exc).__name__}: {exc}", clock())
                summary["failed" if outcome == "failed" else "retry"] += 1
                continue
            try:
                async with session_scope() as s:
                    _, lead_ids = await store_city(
                        s,
                        settings,
                        adapters,
                        claim,
                        found,
                        now=clock(),
                        mirror=mirror,
                        elements=elements,
                    )
            except Exception as exc:  # rolled back as a whole: the city is simply retried
                logger.exception("osm sweep: storing %s, %s failed", claim.city, claim.country)
                outcome = await _fail(claim, settings, f"store: {type(exc).__name__}", clock())
                summary["failed" if outcome == "failed" else "retry"] += 1
                continue
            summary["done"] += 1
            summary["lead_ids"] += lead_ids
    finally:
        if overpass is None:
            await op.aclose()
    return summary


# ── dashboard status ───────────────────────────────────────────────────────


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def status(session: AsyncSession, settings: Settings, now: datetime) -> dict[str, Any]:
    """Today's countries and cities, this week's results per country and the data sources."""
    today = now.date()
    monday = week_start(today)
    countries = await geonames.rotation_countries(session, settings)
    rows = list(
        (
            await session.execute(
                select(SweepCity)
                .where(SweepCity.day == today)
                .order_by(SweepCity.country, SweepCity.population.desc().nulls_last())
            )
        ).scalars()
    )
    totals = dict.fromkeys(STATUSES, 0)
    for r in rows:
        totals[r.status] = totals.get(r.status, 0) + 1
    backlog = (
        await session.execute(
            select(func.count(SweepCity.id)).where(
                SweepCity.day >= today - timedelta(days=BACKLOG_DAYS),
                SweepCity.day < today,
                SweepCity.status.in_(("pending", "running")),
            )
        )
    ).scalar_one()

    week_rows = (
        await session.execute(
            select(SweepCity.country, SweepCity.status, func.count(SweepCity.id))
            .where(SweepCity.day >= monday, SweepCity.day <= today)
            .group_by(SweepCity.country, SweepCity.status)
        )
    ).all()
    per_country: dict[str, dict[str, int]] = {}
    for country, st, n in week_rows:
        per_country.setdefault(country, dict.fromkeys(STATUSES, 0))[st] = int(n)
    since = datetime.combine(monday, datetime.min.time(), tzinfo=UTC)
    biz = dict(
        (
            await session.execute(
                select(Business.country, func.count(Business.id))
                .where(Business.osm_id.is_not(None), Business.last_seen >= since)
                .group_by(Business.country)
            )
        ).all()
    )
    week = []
    for i, c in enumerate(countries):
        seen = per_country.get(c)
        week.append(
            {
                "country": c,
                "weekday": i % 7,
                "cities": sum(seen.values()) if seen else None,
                "done": seen["done"] if seen else None,
                "failed": seen["failed"] if seen else None,
                # Real counts only for countries swept this week; None is shown as unknown.
                "businesses": int(biz.get(c, 0)) if seen and seen["done"] else None,
            }
        )

    city_count = (await session.execute(select(func.count()).select_from(GeoCity))).scalar_one()
    last = await geonames.last_refresh(session)
    return {
        "enabled": settings.osm_sweep_enabled,
        "day": today.isoformat(),
        "weekday": today.weekday(),
        "week_start": monday.isoformat(),
        "today_countries": geonames.countries_for_day(countries, today),
        "cities": [
            {
                "country": r.country,
                "city": r.city,
                "population": r.population,
                "status": r.status,
                "attempts": r.attempts,
                "counts": r.counts,
                "error": r.error,
                "mirror": r.mirror,
                "next_attempt_at": r.next_attempt_at.isoformat() if r.next_attempt_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ],
        "totals": totals,
        "backlog_pending": int(backlog),
        "week": week,
        "geonames": {
            "cities": int(city_count),
            "countries": len(countries),
            "refreshed_at": last.isoformat() if last else None,
            "source_url": settings.geonames_cities_url,
        },
        "settings": {
            "radius_m": settings.osm_sweep_radius_m,
            "cities_per_country": settings.osm_sweep_cities_per_country,
            "daily_lead_cap": settings.osm_sweep_daily_lead_cap,
            "max_attempts": settings.osm_sweep_max_attempts,
        },
        "attribution": {
            "geonames": settings.geonames_attribution,
            "osm": settings.osm_attribution,
        },
    }


async def run_job(
    settings: Settings,
    adapters: Adapters,
    *,
    max_cities: int | None = None,
    budget_s: float = JOB_BUDGET_S,
) -> dict[str, Any]:
    """One sweep run: load the cities on first use, sweep a batch, queue the new leads for
    the fast lane."""
    if not settings.osm_sweep_enabled:
        return {"status": "disabled"}
    async with session_scope() as s:
        loaded = await geonames.last_refresh(s) is not None
    if not loaded:
        try:
            await geonames.refresh(settings)
        except (geonames.GeoNamesError, httpx.HTTPError, OSError) as exc:
            logger.warning("osm sweep: GeoNames cities unavailable: %s", exc)
            return {"status": "no_cities", "error": str(exc)[:300]}
    summary = await run_batch(settings, adapters, budget_s=budget_s, max_cities=max_cities)
    lead_ids: list[uuid.UUID] = summary.pop("lead_ids", [])
    summary["leads"] = len(lead_ids)
    if lead_ids:
        from app.orchestrator import lanes  # noqa: PLC0415 - keeps the API import light

        try:
            await lanes.enqueue_fast(lead_ids)
        except Exception:  # Redis down: the fast-lane sweep picks never-queued leads up
            logger.warning("osm sweep: %d lead(s) left for the fast-lane sweep", len(lead_ids))
    return summary
