"""Leads-per-platform report for the social platforms (Apify, official APIs and the
licensed social data providers)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Brief, Lead, RunStatus, Source, SourceRun
from app.schemas import PlatformReport, PlatformStat
from app.services.not_useful_leads import working

QUALIFIED_MIN, HIGH_MIN = 60, 75

#: platform → (label, source keys). Apify sources and the official APIs report together.
PROVIDERS_SOURCE = "social_providers"
PLATFORMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "x": ("X", ("social_x", "x", f"{PROVIDERS_SOURCE}:x")),
    "reddit": ("Reddit", ("social_reddit", "reddit", f"{PROVIDERS_SOURCE}:reddit")),
    "instagram": ("Instagram", ("social_instagram", f"{PROVIDERS_SOURCE}:instagram")),
    "facebook": ("Facebook", ("social_facebook", "meta", f"{PROVIDERS_SOURCE}:facebook")),
    "tiktok": ("TikTok", (f"{PROVIDERS_SOURCE}:tiktok",)),
    "threads": ("Threads", (f"{PROVIDERS_SOURCE}:threads",)),
    "youtube": ("YouTube", (f"{PROVIDERS_SOURCE}:youtube",)),
}
# Leads from the providers source report under "social_providers:<platform>" (the platform is
# the prefix of their source_item_id).
_LEAD_KEY = case(
    (
        Lead.source == PROVIDERS_SOURCE,
        literal(f"{PROVIDERS_SOURCE}:") + func.split_part(Lead.source_item_id, ":", 1),
    ),
    else_=Lead.source,
)


async def platform_report(
    session: AsyncSession, days: int = 7, *, recent_hours: int = 8
) -> PlatformReport:
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    day0 = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    keys = [k for _, ks in PLATFORMS.values() for k in ks]
    lead_sources = [k for k in keys if ":" not in k] + [PROVIDERS_SOURCE]

    seller = Brief.data["intent"]["signals"].has_key("seller_not_buyer")
    # Lead counts are working leads only; "sellers filtered" counts what was dropped on purpose.
    live = working()
    lead_rows = (
        await session.execute(
            select(
                _LEAD_KEY,
                func.count().filter(live),
                func.count().filter(live, Lead.created_at >= now - timedelta(hours=recent_hours)),
                func.count().filter(live, Lead.intent_score >= QUALIFIED_MIN),
                func.count().filter(live, Lead.intent_score >= HIGH_MIN),
                func.count().filter(seller),
            )
            .outerjoin(Brief, Brief.lead_id == Lead.id)
            .where(Lead.source.in_(lead_sources), Lead.created_at >= since)
            .group_by(_LEAD_KEY)
        )
    ).all()
    by_source = {r[0]: tuple(int(v) for v in r[1:]) for r in lead_rows}

    day = func.date_trunc("day", Lead.created_at)
    daily_rows = (
        await session.execute(
            select(_LEAD_KEY, day, func.count())
            .where(Lead.source.in_(lead_sources), Lead.created_at >= day0, working())
            .group_by(_LEAD_KEY, day)
        )
    ).all()

    run_rows = (
        await session.execute(
            select(
                SourceRun.source_key,
                func.coalesce(func.sum(SourceRun.items_seen), 0),
                func.count(),
                func.sum(case((SourceRun.status == RunStatus.FAILED, 1), else_=0)),
            )
            .where(SourceRun.source_key.in_(keys), SourceRun.started_at >= since)
            .group_by(SourceRun.source_key)
        )
    ).all()
    runs = {r[0]: (int(r[1]), int(r[2]), int(r[3] or 0)) for r in run_rows}
    sources = {
        s.key: s
        for s in (
            await session.execute(select(Source).where(Source.key.in_(lead_sources)))
        ).scalars()
    }

    out: list[PlatformStat] = []
    for platform, (label, ks) in PLATFORMS.items():
        totals = [sum(by_source.get(k, (0,) * 5)[i] for k in ks) for i in range(5)]
        daily = [0] * days
        for src, d, n in daily_rows:
            if src in ks:
                idx = (d.replace(tzinfo=d.tzinfo or UTC) - day0).days
                if 0 <= idx < days:
                    daily[idx] += int(n)
        rows = [sources[k] for k in ks if k in sources]
        if any(k.startswith(f"{PROVIDERS_SOURCE}:") for k in ks) and PROVIDERS_SOURCE in sources:
            rows.append(sources[PROVIDERS_SOURCE])
        last = max((s for s in rows if s.last_run_at), key=lambda s: s.last_run_at, default=None)  # type: ignore[arg-type, return-value]
        out.append(
            PlatformStat(
                platform=platform,
                label=label,
                sources=[x.key for x in rows] or list(ks),
                enabled=any(s.enabled for s in rows),
                leads=totals[0],
                leads_recent=totals[1],
                qualified=totals[2],
                high=totals[3],
                sellers_filtered=totals[4],
                items_seen=sum(runs.get(k, (0, 0, 0))[0] for k in ks),
                runs=sum(runs.get(k, (0, 0, 0))[1] for k in ks),
                failed_runs=sum(runs.get(k, (0, 0, 0))[2] for k in ks),
                last_run_at=last.last_run_at if last else None,
                last_status=last.last_status if last else None,
                daily=daily,
            )
        )
    return PlatformReport(days=days, recent_hours=recent_hours, platforms=out)
