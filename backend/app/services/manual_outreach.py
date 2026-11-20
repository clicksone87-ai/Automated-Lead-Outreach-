"""Manual Track B outreach queue.

This module only stores copy-ready drafts and records human completion. It never
posts comments, sends DMs or calls any outbound social/email posting adapter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Business,
    Lead,
    ManualOutreach,
    ManualOutreachStatus,
    Proposal,
)


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


async def list_queue(session: AsyncSession, status: str = "pending") -> list[dict[str, object]]:
    if status not in {"pending", "done", "skipped"}:
        raise ValueError("invalid status")
    rows = (
        await session.execute(
            select(ManualOutreach, Lead, Business)
            .join(Lead, Lead.id == ManualOutreach.lead_id)
            .outerjoin(Business, Business.id == Lead.business_id)
            .where(ManualOutreach.status == status)
            .order_by(ManualOutreach.created_at.asc())
        )
    ).all()
    out: list[dict[str, object]] = []
    for item, lead, business in rows:
        drafts = item.drafts if isinstance(item.drafts, dict) else {}
        out.append(
            {
                "id": item.id,
                "lead_id": lead.id,
                "business_name": business.name if business else None,
                "channel": item.channel,
                "post_link": item.post_link,
                "comment": drafts.get("comment", ""),
                "dm": drafts.get("dm", ""),
                "contact_form": drafts.get("contact_form", ""),
                "status": item.status,
                "done_at": item.done_at,
                "done_by": item.done_by,
                "created_at": item.created_at,
            }
        )
    return out


async def set_status(
    session: AsyncSession,
    item_id: uuid.UUID,
    status: ManualOutreachStatus,
    done_by: str,
) -> dict[str, object] | None:
    item = await session.get(ManualOutreach, item_id)
    if item is None:
        return None
    lead = await session.get(Lead, item.lead_id)
    if lead is None:
        return None
    business = await session.get(Business, lead.business_id) if lead.business_id else None
    item.status = status
    item.done_at = (
        datetime.now(UTC)
        if status
        in (
            ManualOutreachStatus.DONE,
            ManualOutreachStatus.SKIPPED,
        )
        else None
    )
    item.done_by = done_by
    await session.flush()
    drafts = item.drafts if isinstance(item.drafts, dict) else {}
    return {
        "id": item.id,
        "lead_id": lead.id,
        "business_name": business.name if business else None,
        "channel": item.channel,
        "post_link": item.post_link,
        "comment": drafts.get("comment", ""),
        "dm": drafts.get("dm", ""),
        "contact_form": drafts.get("contact_form", ""),
        "status": item.status,
        "done_at": item.done_at,
        "done_by": item.done_by,
        "created_at": item.created_at,
    }
