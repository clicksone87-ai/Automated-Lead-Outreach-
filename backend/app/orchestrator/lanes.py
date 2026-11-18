"""Fast lane vs heavy lane (V2 Speed and volume).

Fast lane (every lead, target < 3 minutes): requirements → contact enrichment → proposal
(a light first draft: email, or Track B manual drafts) → outreach.

Heavy lane (top leads only, or on demand from the dashboard): Playwright audit, logo concepts,
website preview build and their quality check, then the proposal is redrafted with those
assets — unless a human already approved or sent the fast-lane draft.

Heavy work runs on its own queue and worker so it never delays fast-lane leads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApprovalStatus, Brief, Business, Lead, Message, Proposal
from app.orchestrator.agents import PIPELINE_ORDER
from app.orchestrator.runs import last_success

HEAVY_STEPS: tuple[str, ...] = ("audit", "branding", "website_builder", "quality_check")
FAST_LANE: tuple[str, ...] = tuple(s for s in PIPELINE_ORDER if s not in HEAVY_STEPS)
HEAVY_LANE: tuple[str, ...] = (*HEAVY_STEPS, "proposal", "outreach")
FULL: tuple[str, ...] = PIPELINE_ORDER

#: "High intent" tier on the dashboard (families.ts): 75+.
HEAVY_MIN_SCORE = 75
#: A queued/running marker older than this is treated as dead (worker crash) and may re-queue.
STALE_AFTER = timedelta(hours=2)

STATE_KEY = "heavy"  # lead.pipeline_state[STATE_KEY] = {"status", "at", "reason"}
ACTIVE = ("queued", "running")


def heavy_steps_in(brief: dict[str, Any] | None) -> list[str]:
    return [s for s in HEAVY_STEPS if s in (brief or {}).get("agents_to_run", [])]


def state(lead: Lead) -> dict[str, Any]:
    value = (lead.pipeline_state or {}).get(STATE_KEY)
    return value if isinstance(value, dict) else {}


def set_state(lead: Lead, status: str, reason: str = "", **extra: Any) -> None:
    current = state(lead)
    lead.pipeline_state = {  # new dict: JSONB change tracking
        **(lead.pipeline_state or {}),
        STATE_KEY: {
            **current,
            **extra,
            "status": status,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        },
    }


def is_active(lead: Lead, now: datetime | None = None) -> bool:
    st = state(lead)
    if st.get("status") not in ACTIVE:
        return False
    try:
        at = datetime.fromisoformat(str(st.get("at")))
    except ValueError:
        return False
    return (now or datetime.now(UTC)) - at < STALE_AFTER


def qualifies(lead: Lead, brief: dict[str, Any] | None) -> bool:
    """Automatic heavy lane: high-intent leads whose brief needs heavy work, or a human asked."""
    if not heavy_steps_in(brief):
        return False
    return bool(state(lead).get("requested")) or (lead.intent_score or 0) >= HEAVY_MIN_SCORE


async def outstanding(session: AsyncSession, lead: Lead, brief: dict[str, Any] | None) -> bool:
    """True while this lead is due heavy work that has not finished yet.

    The proposal agent uses it to hold the fast-lane draft for a human instead of
    auto-approving it, so a top lead's email waits for its previews.
    """
    if not qualifies(lead, brief):
        return False
    for step in heavy_steps_in(brief):
        if await last_success(session, step, lead.id) is None:
            return True
    return False


async def request(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    """Dashboard "Generate preview": add logo + website (and audit when they have a site)."""
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise KeyError(lead_id)
    brief_row = await session.get(Brief, lead_id)
    if brief_row is None:
        raise LookupError("lead has no brief yet — wait for the Requirements agent")
    business = await session.get(Business, lead.business_id) if lead.business_id else None
    wanted = ["branding", "website_builder", "quality_check"]
    if business is not None and (business.domain or business.has_website):
        wanted.insert(0, "audit")
    agents = list(brief_row.data.get("agents_to_run", []))
    for step in wanted:
        if step not in agents:
            agents.append(step)
    ordered = [s for s in PIPELINE_ORDER if s in agents]
    brief_row.data = {**brief_row.data, "agents_to_run": ordered}
    set_state(lead, "queued", "requested from the dashboard", requested=True)
    await session.flush()
    return lead


async def supersede_pending_proposal(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """Retire the fast-lane draft so the heavy lane can redraft with assets.

    Only an unapproved, unsent draft without assets (a fast-lane draft) is retired. Returns
    True when the proposal step should run again.
    """
    latest = (
        await session.execute(
            select(Proposal)
            .where(Proposal.lead_id == lead_id)
            .order_by(Proposal.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        latest is None
        or latest.attachments  # already drafted with assets
        or latest.approval_status not in (ApprovalStatus.PENDING, ApprovalStatus.DRAFT)
    ):
        return False
    sent = (
        await session.execute(
            select(func.count()).select_from(Message).where(Message.proposal_id == latest.id)
        )
    ).scalar_one()
    if sent:
        return False
    latest.approval_status = ApprovalStatus.REJECTED
    latest.rejection_reason = "superseded: redrafted with audit, logos and website preview"
    await session.flush()
    return True
