"""Routing into the "Not useful" bucket, the tab that lists it, and the mapping of leads that
were stopped before the bucket existed.

Agents keep deciding *why* a lead stops; `stop` only changes where it lands. Every working view
and count filters with `working()`, so a not-useful lead is only ever seen in its own tab.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, Update, and_, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Business, Lead, LeadStatus
from app.orchestrator.agents import AgentOutcome
from app.services.not_useful import LABELS, NotUsefulReason, mark


def stop(
    lead: Lead,
    reason: NotUsefulReason,
    detail: str | None = None,
    output: dict[str, Any] | None = None,
) -> AgentOutcome:
    """An agent's stop that sends the lead to "Not useful" with its reason. The outcome carries
    the same status and text, so the run bookkeeping writes what `mark` wrote."""
    mark(lead, reason, detail)
    return AgentOutcome(False, LeadStatus.NOT_USEFUL, lead.status_reason or "", dict(output or {}))


def working() -> ColumnElement[bool]:
    """Filter for every working view and count: not-useful leads never appear there."""
    return Lead.status != LeadStatus.NOT_USEFUL


# ── the tab ─────────────────────────────────────────────────────────────────


class NotUsefulRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    business_name: str | None = None
    source: str
    source_url: str
    country: str | None
    language: str | None
    service_needed: list[str]
    intent_score: int | None
    reason: str | None
    reason_label: str | None
    status_reason: str | None
    created_at: datetime
    updated_at: datetime


class NotUsefulPage(BaseModel):
    items: list[NotUsefulRow]
    total: int
    page: int
    page_size: int


class ReasonCount(BaseModel):
    reason: str
    label: str
    count: int


class NotUsefulCounts(BaseModel):
    total: int
    reasons: list[ReasonCount]


def label_for(reason: str | None) -> str | None:
    if reason is None:
        return None
    try:
        return LABELS[NotUsefulReason(reason)]
    except ValueError:
        return reason.replace("_", " ")


async def counts(session: AsyncSession) -> NotUsefulCounts:
    """Per-reason counts straight from the database (every known reason, zero included)."""
    rows = dict(
        (
            await session.execute(
                select(Lead.not_useful_reason, func.count())
                .where(Lead.status == LeadStatus.NOT_USEFUL)
                .group_by(Lead.not_useful_reason)
            )
        ).all()
    )
    reasons = [ReasonCount(reason=r.value, label=LABELS[r], count=int(rows.pop(r.value, 0)))
               for r in NotUsefulReason]  # fmt: skip
    # A row whose reason is unknown to this code (or missing) is still counted, never hidden.
    for key, n in sorted(rows.items(), key=lambda kv: str(kv[0])):
        name = str(key) if key else "unknown"
        reasons.append(ReasonCount(reason=name, label=label_for(name) or name, count=int(n)))
    return NotUsefulCounts(total=sum(r.count for r in reasons), reasons=reasons)


async def list_leads(
    session: AsyncSession,
    *,
    reason: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> NotUsefulPage:
    stmt = (
        select(Lead, Business.name)
        .outerjoin(Business, Business.id == Lead.business_id)
        .where(Lead.status == LeadStatus.NOT_USEFUL)
    )
    if reason == "unknown":
        stmt = stmt.where(Lead.not_useful_reason.is_(None))
    elif reason:
        stmt = stmt.where(Lead.not_useful_reason == reason)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Lead.title.ilike(like), Business.name.ilike(like),
                              Lead.source_url.ilike(like)))  # fmt: skip
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Lead.updated_at.desc(), Lead.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = []
    for lead, name in rows:
        row = NotUsefulRow.model_validate(
            {
                **{f: getattr(lead, f) for f in NotUsefulRow.model_fields if hasattr(lead, f)},
                "business_name": name,
                "reason": lead.not_useful_reason,
                "reason_label": label_for(lead.not_useful_reason),
            }
        )
        items.append(row)
    return NotUsefulPage(items=items, total=int(total), page=page, page_size=page_size)


# ── leads stopped before the bucket existed ─────────────────────────────────
#
# Which old statuses mean "never useful":
#   rejected_low_intent  -> older_than_8h when the prefilter stopped a stale or undated post,
#                           otherwise low_intent
#   no_contact_route     -> no_contact
#   parked_country       -> excluded_market, only for an excluded market (other parked
#                           countries may become targets when Settings change, so they stay)
# Statuses that stay: parked_language (languages can be enabled later), cooldown (temporary),
# duplicate (its business is worked through the other lead), suppressed (permanent
# do-not-contact, kept on the Suppression list), rejected_by_human, failed, paused_budget.

STALE_PREFILTER_REASONS = ("post is % h old (limit % h)", "social post without a timestamp")


def _moved_to(reason: NotUsefulReason) -> dict[str, Any]:
    # The old reason stays readable in brackets; updated_at keeps the time the lead stopped.
    return {
        "status": LeadStatus.NOT_USEFUL.value,
        "not_useful_reason": reason.value,
        "status_reason": literal(f"not useful: {LABELS[reason]}")
        + func.coalesce(literal(" (") + Lead.status_reason + literal(")"), ""),
        "updated_at": Lead.updated_at,
    }


def mapping(excluded: Iterable[str]) -> list[tuple[NotUsefulReason, Update]]:
    """The old-status -> reason rules above, in the order they must run."""
    codes = sorted({c.strip().upper() for c in excluded if c.strip()})
    low = Lead.status == LeadStatus.REJECTED_LOW_INTENT
    stale = or_(
        Lead.prefilter_reason.like(STALE_PREFILTER_REASONS[0]),
        Lead.prefilter_reason == STALE_PREFILTER_REASONS[1],
    )
    excluded_market = and_(
        Lead.status == LeadStatus.PARKED_COUNTRY,
        or_(
            Lead.status_reason.like("%is an excluded market%"),
            func.upper(Lead.country).in_(codes),
        ),
    )
    rules = [
        (NotUsefulReason.OLDER_THAN_8H, and_(low, stale)),
        (NotUsefulReason.LOW_INTENT, low),
        (NotUsefulReason.NO_CONTACT, Lead.status == LeadStatus.NO_CONTACT_ROUTE),
        (NotUsefulReason.EXCLUDED_MARKET, excluded_market),
    ]
    return [
        (
            reason,
            update(Lead)
            .where(where)
            .values(**_moved_to(reason))
            .execution_options(synchronize_session=False),
        )
        for reason, where in rules
    ]


async def map_existing(session: AsyncSession, excluded: Iterable[str]) -> dict[str, int]:
    """Move leads stopped under the old statuses into "Not useful" (idempotent: a mapped lead
    no longer has its old status, so a second run changes nothing)."""
    moved = {r.value: 0 for r in NotUsefulReason}
    for reason, stmt in mapping(excluded):
        result: Any = await session.execute(stmt)
        moved[reason.value] += int(result.rowcount or 0)
    await session.flush()
    return moved
