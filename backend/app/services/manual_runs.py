"""Guard for manual "Run now" on sources that spend a limited free tier (Apify, Serper).

Such a source may be run by hand at most once per hour, and only while its free caps still
cover a whole run. Each decision is logged in `policy_decisions`, which is also where the
hourly limit is read from.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters
from app.agents.discovery.sources import SOURCES
from app.config import Settings
from app.db.models import PolicyDecision, Source
from app.orchestrator import quota

INTERVAL = timedelta(hours=1)
CHECK = "manual_run"


@dataclass(slots=True)
class Need:
    scope: str  # quota scope from orchestrator.quota
    amount: float  # worst case for one run, in the quota's own measure


@dataclass(slots=True)
class ManualRunVerdict:
    allowed: bool
    limited: bool
    reason: str | None = None
    next_allowed_at: datetime | None = None
    usage: list[str] = field(default_factory=list)  # plain-language quota lines for the UI
    status_code: int = 202


def run_needs(key: str, config: dict[str, Any]) -> list[Need]:
    """Worst-case free-tier usage of one run of `key`; empty for sources that are free."""
    src = SOURCES.get(key)
    if src is None:
        return []
    if src.integration == "apify":
        charge = float(config.get("max_charge_usd", 0.1))
        actors = 1
        if key == "social_facebook":  # keyword search + optional public groups and pages
            actors += bool(config.get("public_group_urls")) + bool(config.get("public_page_urls"))
        amount = round(charge * actors, 4)
        return [Need("apify", amount), Need(f"apify:{key}", amount)]
    if key in ("search_api", "local_sites"):
        n = float(config.get("queries_per_run", 1))
        return [Need("serper", n), Need("serper:daily", n)]
    if key == "no_website_check" and config.get("places_lookups", True):
        n = float(config.get("per_run", 10))
        return [Need("serper", n), Need("serper:daily", n)]
    return []


def _fmt(q: quota.Quota, value: float) -> str:
    return f"${value:.2f}" if q.measure == "credit_usd" else f"{value:g}"


async def evaluate(
    session: AsyncSession,
    settings: Settings,
    adapters: Adapters,
    key: str,
    *,
    now: datetime | None = None,
    live: bool = True,
) -> ManualRunVerdict:
    """Would a manual run of `key` be allowed right now? Never records anything."""
    now = now or datetime.now(UTC)
    row = await session.get(Source, key)
    src = SOURCES[key]
    config = dict(row.config if row else src.default_config)
    needs = run_needs(key, config)
    if not needs:
        return ManualRunVerdict(allowed=True, limited=False)

    table = quota.quotas(settings)
    usage: list[str] = []
    short: str | None = None
    for need in needs:
        q = table.get(need.scope)
        if q is None:
            continue
        left = max(0.0, q.cap - await quota.used(session, q, now))
        usage.append(
            f"{q.scope}: {_fmt(q, left)} of {_fmt(q, q.cap)} {q.unit_label} left; "
            f"one run uses up to {_fmt(q, need.amount)}"
        )
        if short is None and left + 1e-9 < need.amount:
            short = (
                f"Free-tier cap: {q.scope} has {_fmt(q, left)} {q.unit_label} left and one run "
                f"can use {_fmt(q, need.amount)}."
            )

    if live and short is None and src.integration == "apify" and adapters.apify is not None:
        account = await adapters.apify.monthly_usage()
        if account and account["used_usd"] >= settings.apify_monthly_cap_usd:
            resets = str(account.get("cycle_end") or "")[:10] or "the next billing cycle"
            short = (
                f"Apify account has used ${account['used_usd']:.2f} of its "
                f"${settings.apify_monthly_cap_usd:.2f} free credit; it resets after {resets}."
            )

    last = (
        await session.execute(
            select(func.max(PolicyDecision.created_at)).where(
                PolicyDecision.check == CHECK,
                PolicyDecision.action == f"source:{key}",
                PolicyDecision.allowed.is_(True),
            )
        )
    ).scalar_one_or_none()
    if last is not None and now - last < INTERVAL:
        nxt = last + INTERVAL
        return ManualRunVerdict(
            allowed=False,
            limited=True,
            reason=f"One manual run per hour for this source. Next one possible at "
            f"{nxt:%H:%M} UTC.",
            next_allowed_at=nxt,
            usage=usage,
            status_code=429,
        )
    if short is not None:
        return ManualRunVerdict(
            allowed=False, limited=True, reason=short, usage=usage, status_code=409
        )
    return ManualRunVerdict(allowed=True, limited=True, usage=usage)


async def authorize(
    session: AsyncSession, settings: Settings, adapters: Adapters, key: str
) -> ManualRunVerdict:
    """Decide and log a manual run. Serialised per source so a double click can't slip past."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(f"manual:{key}".encode())}
    )
    verdict = await evaluate(session, settings, adapters, key)
    if verdict.limited:
        session.add(
            PolicyDecision(
                check=CHECK,
                action=f"source:{key}",
                allowed=verdict.allowed,
                reason=verdict.reason or "manual run allowed",
                details={"usage": verdict.usage},
            )
        )
        await session.flush()
    return verdict
