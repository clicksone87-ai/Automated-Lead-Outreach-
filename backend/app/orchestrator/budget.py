"""Budget caps — every paid call goes through `charge()` first.

Checks, in one serialized transaction (advisory lock):
  global daily cap, global monthly cap, per-agent caps, per-provider caps.
If any cap would be exceeded, the call is refused (BudgetExceededError), the refusal is logged
as a policy decision, and the caller pauses. AI can never override this.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cost, PolicyDecision
from app.services import runtime_settings

_LOCK_KEY = 0x6275646774  # "budgt"


class BudgetExceededError(Exception):
    def __init__(self, scope: str, spent: float, cap: float, amount: float) -> None:
        super().__init__(
            f"budget cap reached for {scope}: spent ${spent:.4f} + ${amount:.4f} > cap ${cap:.2f}"
        )
        self.scope = scope
        self.spent = spent
        self.cap = cap
        self.amount = amount


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    spent_today: float
    spent_month: float
    daily_cap: float
    monthly_cap: float

    @property
    def paused(self) -> bool:
        return self.spent_today >= self.daily_cap or self.spent_month >= self.monthly_cap


def _period_starts(now: datetime) -> tuple[datetime, datetime]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


async def _sum(
    session: AsyncSession,
    since: datetime,
    *,
    agent: str | None = None,
    provider: str | None = None,
) -> float:
    stmt = select(func.coalesce(func.sum(Cost.amount_usd), 0)).where(Cost.created_at >= since)
    if agent:
        stmt = stmt.where(Cost.agent == agent)
    if provider:
        stmt = stmt.where(Cost.provider == provider)
    return float((await session.execute(stmt)).scalar_one())


async def snapshot(session: AsyncSession, now: datetime | None = None) -> BudgetSnapshot:
    now = now or datetime.now(UTC)
    day, month = _period_starts(now)
    rs = await runtime_settings.load(session)
    return BudgetSnapshot(
        spent_today=await _sum(session, day),
        spent_month=await _sum(session, month),
        daily_cap=rs.daily_budget_usd,
        monthly_cap=rs.monthly_budget_usd,
    )


async def charge(
    session: AsyncSession,
    *,
    provider: str,
    agent: str,
    amount_usd: float,
    lead_id: uuid.UUID | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> Cost:
    """Reserve `amount_usd` against all caps, or raise BudgetExceededError."""
    if amount_usd < 0:
        raise ValueError("amount must be >= 0")
    now = now or datetime.now(UTC)
    day, month = _period_starts(now)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    rs = await runtime_settings.load(session)

    checks: list[tuple[str, float, float | None]] = [
        ("global:daily", await _sum(session, day), rs.daily_budget_usd),
        ("global:monthly", await _sum(session, month), rs.monthly_budget_usd),
    ]
    for scope_key, kwargs in (
        (f"agent:{agent}", {"agent": agent}),
        (f"provider:{provider}", {"provider": provider}),
    ):
        scoped = rs.budget_caps.get(scope_key)
        if scoped is None:
            continue
        if scoped.daily_usd is not None:
            checks.append(
                (f"{scope_key}:daily", await _sum(session, day, **kwargs), scoped.daily_usd)
            )
        if scoped.monthly_usd is not None:
            checks.append(
                (f"{scope_key}:monthly", await _sum(session, month, **kwargs), scoped.monthly_usd)
            )

    for scope, spent, cap in checks:
        if cap is not None and spent + amount_usd > cap + 1e-9:
            session.add(
                PolicyDecision(
                    check="budget",
                    action=f"{agent}:{provider}",
                    lead_id=lead_id,
                    allowed=False,
                    reason=f"{scope} cap ${cap:.2f} reached (spent ${spent:.4f})",
                    details={"amount_usd": amount_usd, "scope": scope},
                )
            )
            await session.flush()
            raise BudgetExceededError(scope, spent, cap, amount_usd)

    cost = Cost(
        provider=provider,
        agent=agent,
        lead_id=lead_id,
        amount_usd=amount_usd,
        description=description,
    )
    session.add(cost)
    await session.flush()
    return cost


async def settle(session: AsyncSession, reserved: Cost, actual_usd: float) -> None:
    """Record the difference between the reserved estimate and the actual cost."""
    delta = round(actual_usd - float(reserved.amount_usd), 6)
    if abs(delta) < 1e-6:
        return
    session.add(
        Cost(
            provider=reserved.provider,
            agent=reserved.agent,
            lead_id=reserved.lead_id,
            amount_usd=delta,
            description=f"settlement for cost #{reserved.id}",
        )
    )
    await session.flush()
