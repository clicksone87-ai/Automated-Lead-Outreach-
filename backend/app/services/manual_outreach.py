"""Manual Track B outreach queue.

This module only stores copy-ready drafts and records what a person did. It never posts
comments, sends DMs or calls any outbound social/email posting adapter.

Steps on a queue item: a person confirms the contact route works, asks for assets (logo,
website preview or both; built on the heavy queue, then the message is redrafted with them),
sends the message on the platform by hand and marks it as sent, which starts the cooldown.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentRun,
    ApprovalStatus,
    Business,
    Lead,
    LeadStatus,
    ManualOutreach,
    ManualOutreachStatus,
    Proposal,
    RunStatus,
)
from app.orchestrator import lanes
from app.services import activity, runtime_settings
from app.services.not_useful import NotUsefulReason, mark
from app.services.not_useful_leads import working

STEP_STATE: dict[str, str] = {
    RunStatus.SUCCEEDED: "done",
    RunStatus.FAILED: "failed",
    RunStatus.SKIPPED: "skipped",
    RunStatus.RUNNING: "running",
    RunStatus.RETRYING: "running",
    RunStatus.QUEUED: "waiting",
}


class ManualOutreachError(Exception):
    """The step is not allowed in the item's current state (HTTP 409)."""


def _track_b_drafts(proposal: Proposal) -> dict[str, Any] | None:
    draft = proposal.draft if isinstance(proposal.draft, dict) else {}
    track_b = draft.get("track_b")
    if not isinstance(track_b, dict):
        return None
    comment = track_b.get("comment")
    dm = track_b.get("dm")
    contact_form = track_b.get("contact_form")
    route = track_b.get("route")
    channel = track_b.get("channel")
    if not any(isinstance(v, str) and v for v in (comment, dm, contact_form)):
        return None
    return {
        "comment": comment if isinstance(comment, str) else "",
        "dm": dm if isinstance(dm, str) else "",
        "contact_form": contact_form if isinstance(contact_form, str) else "",
        "post_link": route if isinstance(route, str) and route else None,
        "channel": channel if isinstance(channel, str) and channel else "manual",
    }


async def sync_queue(session: AsyncSession) -> int:
    """Create/update queue rows from the latest Track B proposal for each lead."""
    proposals = (
        await session.execute(
            select(Proposal, Lead, Business)
            .join(Lead, Lead.id == Proposal.lead_id)
            .outerjoin(Business, Business.id == Lead.business_id)
            .order_by(Proposal.created_at.desc(), Proposal.version.desc())
        )
    ).all()

    seen: set[uuid.UUID] = set()
    changed = 0
    for proposal, lead, _business in proposals:
        if lead.id in seen:
            continue
        seen.add(lead.id)
        track_b = _track_b_drafts(proposal)
        if track_b is None:
            continue
        existing = (
            await session.execute(
                select(ManualOutreach).where(
                    ManualOutreach.lead_id == lead.id,
                    ManualOutreach.channel == track_b["channel"],
                )
            )
        ).scalar_one_or_none()
        drafts = {
            "comment": track_b["comment"],
            "dm": track_b["dm"],
            "contact_form": track_b["contact_form"],
        }
        if existing is None:
            session.add(
                ManualOutreach(
                    lead_id=lead.id,
                    channel=track_b["channel"],
                    post_link=track_b["post_link"],
                    drafts=drafts,
                    status=ManualOutreachStatus.PENDING,
                )
            )
            changed += 1
        elif existing.post_link != track_b["post_link"] or existing.drafts != drafts:
            existing.post_link = track_b["post_link"]
            existing.drafts = drafts
            changed += 1
    await session.flush()
    return changed


# ── reading the queue ───────────────────────────────────────────────────────


async def _latest_proposal(session: AsyncSession, lead_id: uuid.UUID) -> Proposal | None:
    return (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.lead_id == lead_id, Proposal.approval_status != ApprovalStatus.SUPERSEDED
            )
            .order_by(Proposal.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _progress(
    session: AsyncSession, lead: Lead, requested: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Where the requested assets are: the heavy job's state plus each of its steps since the
    request (the final step is the message redrafted with the assets)."""
    if not requested:
        return None
    try:
        since = datetime.fromisoformat(str(requested.get("at")))
    except ValueError:
        since = datetime.min.replace(tzinfo=UTC)
    steps = [*requested.get("steps", []), "proposal"]
    runs = (
        await session.execute(
            select(AgentRun.agent, AgentRun.status)
            .where(
                AgentRun.lead_id == lead.id,
                AgentRun.agent.in_(steps),
                AgentRun.started_at >= since,
            )
            .order_by(AgentRun.started_at)
        )
    ).tuples()
    latest: dict[str, str] = dict(runs.all())  # ordered by start: the last run wins
    heavy = lanes.state(lead)
    return {
        "status": heavy.get("status") or "queued",
        "reason": heavy.get("reason") or "",
        "steps": [
            {
                "step": step,
                "state": STEP_STATE.get(latest[step], "waiting") if step in latest else "waiting",
            }
            for step in steps
        ],
    }


def _next_step(item: ManualOutreach, progress: dict[str, Any] | None) -> str:
    """What the person does next (the dashboard shows the matching button)."""
    if item.sent_at is not None:
        return "sent"
    if item.status != ManualOutreachStatus.PENDING:
        return str(item.status)
    if progress is not None:
        status = progress["status"]
        if status in ("queued", "running"):
            return "preparing_assets"
        if status == "done":
            return "ready_to_send"
        return "assets_failed"
    if item.contact_confirmed_at is not None:
        return "prepare_assets"
    return "confirm_contact"


async def _item_out(
    session: AsyncSession, item: ManualOutreach, lead: Lead, business: Business | None
) -> dict[str, object]:
    drafts = item.drafts if isinstance(item.drafts, dict) else {}
    proposal = await _latest_proposal(session, lead.id)
    progress = await _progress(session, lead, item.assets_requested)
    services = list(lead.service_needed or [])
    return {
        "id": item.id,
        "lead_id": lead.id,
        "business_name": business.name if business else None,
        "lead_status": lead.status,
        "country": lead.country,
        "services": services,
        "channel": item.channel,
        "post_link": item.post_link,
        "comment": drafts.get("comment", ""),
        "dm": drafts.get("dm", ""),
        "contact_form": drafts.get("contact_form", ""),
        "status": item.status,
        "step": _next_step(item, progress),
        "contact_confirmed_at": item.contact_confirmed_at,
        "contact_confirmed_by": item.contact_confirmed_by,
        "assets_suggested": lanes.suggested_kinds(services),
        "assets_requested": item.assets_requested,
        "assets_progress": progress,
        "attachments": [
            {"kind": a.get("kind"), "filename": a.get("filename"), "url": f"/api/files/{a['path']}"}
            for a in (proposal.attachments if proposal else []) or []
            if isinstance(a, dict) and a.get("path")
        ],
        "sent_at": item.sent_at,
        "sent_by": item.sent_by,
        "done_at": item.done_at,
        "done_by": item.done_by,
        "created_at": item.created_at,
    }


async def list_queue(session: AsyncSession, status: str = "pending") -> list[dict[str, object]]:
    if status not in {"pending", "done", "skipped"}:
        raise ValueError("invalid status")
    rows = (
        await session.execute(
            select(ManualOutreach, Lead, Business)
            .join(Lead, Lead.id == ManualOutreach.lead_id)
            .outerjoin(Business, Business.id == Lead.business_id)
            .where(ManualOutreach.status == status, working())
            .order_by(ManualOutreach.created_at.asc())
        )
    ).all()
    return [await _item_out(session, item, lead, business) for item, lead, business in rows]


async def _load(
    session: AsyncSession, item_id: uuid.UUID
) -> tuple[ManualOutreach, Lead, Business | None] | None:
    item = await session.get(ManualOutreach, item_id)
    if item is None:
        return None
    lead = await session.get(Lead, item.lead_id)
    if lead is None:
        return None
    business = await session.get(Business, lead.business_id) if lead.business_id else None
    return item, lead, business


def _require_pending(item: ManualOutreach) -> None:
    if item.status != ManualOutreachStatus.PENDING:
        raise ManualOutreachError(f"item is {item.status}")


# ── the person's steps ──────────────────────────────────────────────────────


async def set_status(
    session: AsyncSession,
    item_id: uuid.UUID,
    status: ManualOutreachStatus,
    done_by: str,
) -> dict[str, object] | None:
    loaded = await _load(session, item_id)
    if loaded is None:
        return None
    item, lead, business = loaded
    item.status = status
    item.done_at = (
        datetime.now(UTC)
        if status in (ManualOutreachStatus.DONE, ManualOutreachStatus.SKIPPED)
        else None
    )
    item.done_by = done_by
    await session.flush()
    return await _item_out(session, item, lead, business)


async def confirm_contact(
    session: AsyncSession, item_id: uuid.UUID, by: str
) -> dict[str, object] | None:
    """The person checked the route reaches the lead; assets may now be prepared."""
    loaded = await _load(session, item_id)
    if loaded is None:
        return None
    item, lead, business = loaded
    _require_pending(item)
    if item.contact_confirmed_at is None:
        item.contact_confirmed_at, item.contact_confirmed_by = datetime.now(UTC), by
        await activity.emit(session, "manual_contact_confirmed",
                            f"Contact route confirmed on {item.channel} ({by})", lead_id=lead.id)  # fmt: skip
    await session.flush()
    return await _item_out(session, item, lead, business)


async def contact_failed(
    session: AsyncSession, item_id: uuid.UUID, by: str
) -> dict[str, object] | None:
    """The route does not reach the lead: the item is skipped and, with no other way to reach
    them, the lead is not useful."""
    loaded = await _load(session, item_id)
    if loaded is None:
        return None
    item, lead, business = loaded
    _require_pending(item)
    now = datetime.now(UTC)
    item.status, item.done_at, item.done_by = ManualOutreachStatus.SKIPPED, now, by
    mark(lead, NotUsefulReason.NO_CONTACT, f"{item.channel} route did not work, checked by {by}")
    await activity.emit(session, "manual_contact_failed",
                        f"Contact route on {item.channel} did not work ({by})", lead_id=lead.id)  # fmt: skip
    await session.flush()
    return await _item_out(session, item, lead, business)


async def prepare_assets(
    session: AsyncSession, item_id: uuid.UUID, kinds: Iterable[str], by: str
) -> dict[str, object] | None:
    """Record the person's choice and set the brief up for exactly those assets. The caller
    commits and then queues the heavy job (never runs it in the request)."""
    loaded = await _load(session, item_id)
    if loaded is None:
        return None
    item, lead, business = loaded
    _require_pending(item)
    if item.contact_confirmed_at is None:
        raise ManualOutreachError("confirm that the contact works first")
    chosen = [k for k in lanes.ASSET_KINDS if k in set(kinds)]
    if not chosen:
        raise ManualOutreachError(f"choose at least one of: {', '.join(lanes.ASSET_KINDS)}")
    try:
        await lanes.request_assets(session, lead.id, kinds=chosen)
    except lanes.AssetsRequestError as exc:
        raise ManualOutreachError(str(exc)) from exc
    business_site = business is not None and bool(business.domain or business.has_website)
    item.assets_requested = {
        "kinds": chosen,
        "steps": lanes.steps_for_kinds(chosen, has_site=business_site),
        "at": datetime.now(UTC).isoformat(),
        "by": by,
    }
    await activity.emit(session, "manual_assets_requested",
                        f"Assets requested for manual outreach: {', '.join(chosen)} ({by})",
                        lead_id=lead.id)  # fmt: skip
    await session.flush()
    return await _item_out(session, item, lead, business)


async def mark_sent(session: AsyncSession, item_id: uuid.UUID, by: str) -> dict[str, object] | None:
    """The person sent the message on the platform: record it and start the cooldown, as
    `sending.mark_sent` does for a Gmail send."""
    loaded = await _load(session, item_id)
    if loaded is None:
        return None
    item, lead, business = loaded
    _require_pending(item)
    now = datetime.now(UTC)
    item.sent_at, item.sent_by = now, by
    item.status, item.done_at, item.done_by = ManualOutreachStatus.DONE, now, by
    lead.status, lead.status_reason = LeadStatus.SENT, f"sent by hand on {item.channel} ({by})"
    if business is not None:
        rs = await runtime_settings.load(session)
        business.cooldown_until = now + timedelta(days=rs.cooldown_days)
    await activity.emit(session, "manual_sent", f"Message sent by hand on {item.channel} "
                        f"(marked by {by})", lead_id=lead.id, agent="outreach")  # fmt: skip
    await session.flush()
    return await _item_out(session, item, lead, business)
