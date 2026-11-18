"""Free-tier quotas: every free-tier provider call goes through `consume()` (or reserve/settle).

Calls are tracked against each provider's own free cap. Exceeding one raises
FreeQuotaExceededError, a BudgetExceededError, so callers pause exactly as for budget caps.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import make_transient

from app.config import Settings, get_settings
from app.db.models import Cost, PolicyDecision
from app.orchestrator.budget import BudgetExceededError

_LOCK_KEY = 0x71756F7461  # "quota"
Period = Literal["day", "month", "lifetime"]
Measure = Literal["units", "credit_usd"]

# Apify monthly free credit split per social source (fractions of APIFY_MONTHLY_CAP_USD).
APIFY_SOURCE_SHARE: dict[str, float] = {
    "social_reddit": 0.3,
    "social_facebook": 0.3,
    "social_x": 0.2,
    "social_instagram": 0.2,
}


# Gemini's daily request cap split per lane, so heavy-lane logos can never starve scoring and
# drafting (fractions of GEMINI_DAILY_REQUEST_CAP; the provider-wide cap still applies).
GEMINI_LANE_SHARE: dict[str, float] = {
    "scoring": 0.15,
    "fast": 0.45,
    "heavy": 0.25,
    "other": 0.15,
}
# Which lane an agent's LLM calls count against when the request does not say.
AGENT_LANE: dict[str, str] = {
    "requirements": "scoring",
    "proposal": "fast",
    "audit": "heavy",
    "branding": "heavy",
    "website_builder": "heavy",
    "quality_check": "heavy",
}


def gemini_scope(agent: str, lane: str | None = None) -> str:
    return f"gemini:{lane or AGENT_LANE.get(agent, 'other')}"


class FreeQuotaExceededError(BudgetExceededError):
    pass


@dataclass(frozen=True, slots=True)
class Quota:
    provider: str
    scope: str  # provider, or provider:sub-scope (e.g. apify:social_reddit)
    period: Period
    measure: Measure
    cap: float
    unit_label: str
    # True: only usage recorded under this exact scope counts. False: all of the provider's
    # usage counts (provider totals and provider-wide daily ceilings).
    by_scope: bool = False


def quotas(s: Settings) -> dict[str, Quota]:
    q = [
        Quota("gemini", "gemini", "day", "units", s.gemini_daily_request_cap, "requests/day"),
        Quota("serper", "serper", "lifetime", "units", s.serper_free_credits, "credits"),
        Quota("serper", "serper:daily", "day", "units", s.serper_daily_credit_cap, "credits/day"),
        Quota("exa", "exa", "lifetime", "credit_usd", s.exa_free_credit_usd, "USD credit"),
        Quota("apify", "apify", "month", "credit_usd", s.apify_monthly_cap_usd, "USD/month"),
        Quota("pagespeed", "pagespeed", "day", "units", s.pagespeed_daily_cap, "requests/day"),
    ]
    q += [
        Quota(
            "apify",
            f"apify:{src}",
            "month",
            "credit_usd",
            round(s.apify_monthly_cap_usd * share, 4),
            "USD/month",
            by_scope=True,
        )
        for src, share in APIFY_SOURCE_SHARE.items()
    ]
    q += [
        Quota(
            "gemini",
            f"gemini:{lane}",
            "day",
            "units",
            max(1.0, float(int(s.gemini_daily_request_cap * share))),
            "requests/day",
            by_scope=True,
        )
        for lane, share in GEMINI_LANE_SHARE.items()
    ]
    return {x.scope: x for x in q}


def applicable(table: dict[str, Quota], provider: str, scope: str | None) -> list[Quota]:
    """Every cap a call to `provider` (optionally under `scope`) must fit inside."""
    keys = [provider, f"{provider}:daily", scope or ""]
    seen: set[str] = set()
    out = []
    for k in keys:
        q = table.get(k)
        if q is not None and q.scope not in seen:
            seen.add(q.scope)
            out.append(q)
    return out


def _since(period: Period, now: datetime) -> datetime | None:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day
    if period == "month":
        return day.replace(day=1)
    return None


async def used(session: AsyncSession, quota: Quota, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    col = Cost.units if quota.measure == "units" else Cost.credit_usd
    stmt = select(func.coalesce(func.sum(col), 0)).where(Cost.free_tier.is_(True))
    if quota.by_scope:
        stmt = stmt.where(Cost.quota_scope == quota.scope)
    else:
        stmt = stmt.where(Cost.provider == quota.provider)
    since = _since(quota.period, now)
    if since is not None:
        stmt = stmt.where(Cost.created_at >= since)
    return float((await session.execute(stmt)).scalar_one())


async def consume(
    session: AsyncSession,
    provider: str,
    *,
    agent: str,
    units: float = 1.0,
    credit_usd: float = 0.0,
    scope: str | None = None,
    lead_id: uuid.UUID | None = None,
    description: str | None = None,
    settings: Settings | None = None,
) -> Cost:
    """Check every applicable free cap, then record the usage. Raises FreeQuotaExceededError.

    The check and the record are committed in their own short transaction, not the caller's:
    the cap lock is held for milliseconds instead of a whole pipeline step (which serialised
    every worker), and a call that was really made stays counted even if the step fails.
    `session` is unused and kept so call sites read the same as budget.charge().
    """
    if units < 0 or credit_usd < 0:
        raise ValueError("usage must be >= 0")
    s = settings or get_settings()
    async with _own_transaction() as own:
        await own.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        for q in applicable(quotas(s), provider, scope):
            amount = units if q.measure == "units" else credit_usd
            if amount <= 0:
                continue
            spent = await used(own, q)
            if spent + amount > q.cap + 1e-9:
                await _insert(
                    own,
                    PolicyDecision(
                        check="free_quota",
                        action=f"{agent}:{provider}",
                        lead_id=lead_id,
                        allowed=False,
                        reason=f"{q.scope} free cap {q.cap:g} {q.unit_label} reached "
                        f"(used {spent:g})",
                        details={"scope": q.scope, "amount": amount},
                    ),
                )
                await own.commit()  # keep the refusal on record
                raise FreeQuotaExceededError(q.scope, spent, q.cap, amount)
        row = Cost(
            provider=provider,
            agent=agent,
            lead_id=lead_id,
            amount_usd=0,
            free_tier=True,
            units=units,
            credit_usd=credit_usd,
            quota_scope=scope or provider,
            description=description,
        )
        await _insert(own, row)
    return row


async def settle(
    session: AsyncSession,
    reserved: Cost,
    *,
    units: float,
    credit_usd: float,
    description: str | None = None,
) -> None:
    """Replace a reservation's estimate with the actual usage once it is known."""
    reserved.units = max(0.0, units)
    reserved.credit_usd = max(0.0, credit_usd)
    values: dict[str, object] = {"units": reserved.units, "credit_usd": reserved.credit_usd}
    if description is not None:
        reserved.description = values["description"] = description
    async with _own_transaction() as own:
        await own.execute(update(Cost).where(Cost.id == reserved.id).values(**values))


@asynccontextmanager
async def _own_transaction() -> AsyncIterator[AsyncSession]:
    from app.db.session import session_scope  # noqa: PLC0415 - avoids an import cycle

    async with session_scope() as own:
        yield own


async def _insert(own: AsyncSession, row: Cost | PolicyDecision) -> None:
    """Insert, dropping the lead link if that lead isn't committed yet (still being created)."""
    try:
        async with own.begin_nested():
            own.add(row)
            await own.flush()
    except IntegrityError:
        if row.lead_id is None:
            raise
        if row in own:
            own.expunge(row)
        make_transient(row)
        row.lead_id = None
        own.add(row)
        await own.flush()


async def report(
    session: AsyncSession, settings: Settings | None = None
) -> list[dict[str, object]]:
    """Usage vs cap for every quota (Sources page / overview)."""
    s = settings or get_settings()
    out: list[dict[str, object]] = []
    for q in quotas(s).values():
        u = await used(session, q)
        out.append(
            {
                "scope": q.scope,
                "provider": q.provider,
                "period": q.period,
                "unit": q.unit_label,
                "cap": q.cap,
                "used": round(u, 5),
                "remaining": round(max(0.0, q.cap - u), 5),
            }
        )
    return out
