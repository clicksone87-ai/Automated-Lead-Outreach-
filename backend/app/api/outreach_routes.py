"""Approval queue, inbox and deals, suppression list, autonomy and unsubscribe endpoints.

Unsubscribe links are public and idempotent.
"""

from __future__ import annotations

import html as html_lib
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdaptersDep, SessionDep, SettingsDep
from app.db.models import (
    ApprovalStatus,
    Business,
    Deal,
    Lead,
    LeadStatus,
    ManualOutreachStatus,
    Message,
    Proposal,
    Reply,
    Suppression,
)
from app.orchestrator.queue import enqueue_pipeline
from app.services import activity, autonomy
from app.services.manual_outreach import list_queue, set_status, sync_queue
from app.services.outreach_jobs import suppress
from app.services.unsubscribe import read_token

router = APIRouter(prefix="/api")
public = APIRouter()


class ApproveIn(BaseModel):
    approved_by: str = Field(default="dashboard", max_length=100)


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class EditIn(BaseModel):
    subject: str | None = Field(default=None, max_length=90)
    greeting: str | None = Field(default=None, max_length=80)
    intro: str | None = Field(default=None, max_length=1200)
    closing: str | None = Field(default=None, max_length=800)
    signoff: str | None = Field(default=None, max_length=40)


class DealPatch(BaseModel):
    quote: dict[str, Any] | None = None
    contract_status: str | None = Field(default=None, pattern="^(none|sent|signed|declined)$")
    payment_status: str | None = Field(default=None, pattern="^(none|invoiced|paid|refunded)$")
    delivery_status: str | None = Field(default=None, pattern="^(none|in_progress|delivered)$")
    notes: str | None = Field(default=None, max_length=4000)


class SuppressionIn(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    domain: str | None = Field(default=None, max_length=255)
    reason: str = Field(default="manual", max_length=100)


class StageIn(BaseModel):
    stage: int = Field(ge=1, le=3)
    reason: str | None = Field(default=None, max_length=500)


class ManualOutreachActionIn(BaseModel):
    done_by: str = Field(default="dashboard", max_length=100)


def _proposal_out(p: Proposal, lead: Lead, business: Business | None) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "lead_id": str(lead.id),
        "business_name": business.name if business else None,
        "lead_status": lead.status,
        "country": lead.country,
        "language": p.language,
        "version": p.version,
        "subject": p.subject,
        "body_html": p.body_html,
        "body_text": p.body_text,
        "draft": p.draft,
        "claims": p.claims,
        "attachments": p.attachments,
        "to_email": p.to_email,
        "approval_status": p.approval_status,
        "edited_by_human": p.edited_by_human,
        "created_at": p.created_at.isoformat(),
    }


async def _load(
    session: SessionDep, proposal_id: uuid.UUID
) -> tuple[Proposal, Lead, Business | None]:
    p = await session.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proposal not found")
    lead = await session.get(Lead, p.lead_id)
    assert lead is not None
    business = await session.get(Business, lead.business_id) if lead.business_id else None
    return p, lead, business


@router.get("/manual-outreach")
async def manual_outreach(session: SessionDep, state: str = "pending") -> list[dict[str, Any]]:
    await sync_queue(session)
    await session.commit()
    try:
        return await list_queue(session, state)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/manual-outreach/{item_id}/done")
async def manual_outreach_done(
    item_id: uuid.UUID,
    body: ManualOutreachActionIn,
    session: SessionDep,
) -> dict[str, Any]:
    result = await set_status(session, item_id, ManualOutreachStatus.DONE, body.done_by)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "manual outreach item not found")
    await session.commit()
    return result


@router.post("/manual-outreach/{item_id}/skip")
async def manual_outreach_skip(
    item_id: uuid.UUID,
    body: ManualOutreachActionIn,
    session: SessionDep,
) -> dict[str, Any]:
    result = await set_status(session, item_id, ManualOutreachStatus.SKIPPED, body.done_by)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "manual outreach item not found")
    await session.commit()
    return result


@router.get("/approvals")
async def approvals(session: SessionDep, state: str = "pending") -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Proposal, Lead, Business)
            .join(Lead, Lead.id == Proposal.lead_id)
            .outerjoin(Business, Business.id == Lead.business_id)
            .where(Proposal.approval_status == state)
            .order_by(Proposal.created_at)
            .limit(100)
        )
    ).all()
    return [_proposal_out(p, lead, biz) for p, lead, biz in rows]


@router.post("/proposals/{proposal_id}/approve")
async def approve(proposal_id: uuid.UUID, body: ApproveIn, session: SessionDep) -> dict[str, Any]:
    p, lead, biz = await _load(session, proposal_id)
    if p.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"proposal is {p.approval_status}")
    p.approval_status, p.approved_by, p.approved_at = (
        ApprovalStatus.APPROVED,
        body.approved_by,
        datetime.now(UTC),
    )
    lead.status, lead.status_reason = LeadStatus.APPROVED, f"approved by {body.approved_by}"
    await activity.emit(
        session, "proposal_approved", f"Proposal v{p.version} approved", lead_id=lead.id
    )
    await session.commit()
    queued = await enqueue_pipeline(lead.id) if p.to_email else False
    return {**_proposal_out(p, lead, biz), "queued_for_sending": queued}


@router.post("/proposals/{proposal_id}/reject")
async def reject(proposal_id: uuid.UUID, body: RejectIn, session: SessionDep) -> dict[str, Any]:
    p, lead, biz = await _load(session, proposal_id)
    if p.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"proposal is {p.approval_status}")
    p.approval_status, p.rejection_reason = ApprovalStatus.REJECTED, body.reason
    lead.status, lead.status_reason = LeadStatus.REJECTED_BY_HUMAN, f"rejected: {body.reason}"
    await activity.emit(
        session, "proposal_rejected", f"Proposal rejected: {body.reason}", lead_id=lead.id
    )
    return _proposal_out(p, lead, biz)


@router.patch("/proposals/{proposal_id}")
async def edit(
    proposal_id: uuid.UUID,
    body: EditIn,
    session: SessionDep,
    settings: SettingsDep,
    adapters: AdaptersDep,
) -> dict[str, Any]:
    p, lead, biz = await _load(session, proposal_id)
    if p.approval_status != ApprovalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "only pending proposals can be edited")
    draft = {
        **p.draft,
        **{k: v for k, v in body.model_dump().items() if v is not None and k != "subject"},
    }
    subject = body.subject or p.subject
    assert adapters.renderer is not None
    rendered = await adapters.renderer.render(
        "proposal",
        {
            "language": p.language, "subject": subject, "previewText": draft.get("preview_text", subject),
            "greeting": draft.get("greeting", ""), "intro": draft.get("intro", ""),
            "findings": [c["text"] for c in p.claims], "closing": draft.get("closing", ""),
            "signoff": draft.get("signoff", ""),
            "sender": {"name": settings.sender_name, "businessName": settings.sender_business_name,
                       "postalAddress": settings.sender_postal_address, "portfolioUrl": settings.portfolio_url},
            "unsubscribeUrl": draft.get("unsubscribe_url", ""), "previewUrl": draft.get("preview_url"),
            "attachments": {"logos": any(a["kind"] == "logo" for a in p.attachments),
                            "preview": any(a["kind"].startswith("preview") for a in p.attachments),
                            "audit": any(a["kind"] == "audit_pdf" for a in p.attachments)},
        },
    )  # fmt: skip
    p.subject, p.draft, p.body_html, p.body_text, p.edited_by_human = (
        subject,
        draft,
        rendered.html,
        rendered.text,
        True,
    )
    return _proposal_out(p, lead, biz)


@router.get("/inbox")
async def inbox(session: SessionDep) -> list[dict[str, Any]]:
    replies = (
        (
            await session.execute(
                select(Reply)
                .where(Reply.handed_to_inbox.is_(True), Reply.lead_id.is_not(None))
                .order_by(Reply.received_at.desc())
            )
        )
        .scalars()
        .all()
    )
    by_lead: dict[uuid.UUID, list[Reply]] = {}
    for r in replies:
        by_lead.setdefault(r.lead_id, []).append(r)  # type: ignore[arg-type]
    out = []
    for lead_id, rs in by_lead.items():
        lead = await session.get(Lead, lead_id)
        if lead is None:
            continue
        biz = await session.get(Business, lead.business_id) if lead.business_id else None
        deal = (
            await session.execute(select(Deal).where(Deal.lead_id == lead_id))
        ).scalar_one_or_none()
        out.append({
            "lead_id": str(lead_id), "business_name": biz.name if biz else None, "lead_status": lead.status,
            "country": lead.country, "language": lead.language,
            "replies": [{"id": str(r.id), "from_email": r.from_email, "subject": r.subject,
                         "received_at": r.received_at.isoformat(), "classification": r.classification,
                         "reason": r.classification_reason, "text": (r.body_text or "")[:4000]} for r in rs],
            "deal": None if deal is None else {
                "quote": deal.quote, "contract_status": deal.contract_status, "payment_status": deal.payment_status,
                "delivery_status": deal.delivery_status, "notes": deal.notes},
        })  # fmt: skip
    return out


@router.patch("/deals/{lead_id}")
async def patch_deal(lead_id: uuid.UUID, body: DealPatch, session: SessionDep) -> dict[str, Any]:
    deal = (await session.execute(select(Deal).where(Deal.lead_id == lead_id))).scalar_one_or_none()
    if deal is None:
        if await session.get(Lead, lead_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")
        deal = Deal(lead_id=lead_id)
        session.add(deal)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(deal, k, v)
    lead = await session.get(Lead, lead_id)
    if lead is not None and body.payment_status == "paid":
        lead.status = LeadStatus.WON
    await activity.emit(session, "deal_updated", "Deal updated by a human", lead_id=lead_id)
    await session.flush()
    return {"lead_id": str(lead_id), "quote": deal.quote, "contract_status": deal.contract_status,
            "payment_status": deal.payment_status, "delivery_status": deal.delivery_status, "notes": deal.notes}  # fmt: skip


@router.get("/suppressions")
async def suppressions(session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Suppression).order_by(Suppression.created_at.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(r.id), "email": r.email, "domain": r.domain, "business_id": str(r.business_id) if r.business_id else None,
             "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]  # fmt: skip


@router.post("/suppressions", status_code=status.HTTP_201_CREATED)
async def add_suppression(body: SuppressionIn, session: SessionDep) -> dict[str, Any]:
    if not body.email and not body.domain:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "email or domain required")
    row = Suppression(
        email=(body.email or "").lower() or None,
        domain=(body.domain or "").lower() or None,
        reason=body.reason,
    )
    session.add(row)
    await session.flush()
    return {"id": str(row.id), "email": row.email, "domain": row.domain, "reason": row.reason}


@router.get("/autonomy")
async def get_autonomy(session: SessionDep) -> dict[str, Any]:
    return (await autonomy.status(session)).as_dict()


@router.post("/autonomy")
async def set_autonomy(body: StageIn, session: SessionDep) -> dict[str, Any]:
    try:
        st = await autonomy.change_stage(
            session, body.stage, changed_by="dashboard", reason=body.reason
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return st.as_dict()


@router.get("/messages")
async def messages(session: SessionDep, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Message).order_by(Message.created_at.desc()).limit(min(limit, 500))
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(m.id), "to_email": m.to_email, "subject": m.subject, "sent_at": m.sent_at.isoformat() if m.sent_at else None,
             "scheduled_for": m.scheduled_for.isoformat() if m.scheduled_for else None, "delivery_status": m.delivery_status,
             "follow_up_number": m.follow_up_number, "sandbox": m.sandbox} for m in rows]  # fmt: skip


# ── public unsubscribe ──────────────────────────────────────────────────────

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:520px;margin:12vh auto;padding:0 20px;color:#111}}
button{{background:#111;color:#fff;border:0;border-radius:8px;padding:12px 18px;font-size:16px;cursor:pointer}}
p{{line-height:1.5}}</style></head><body><h1>{title}</h1>{body}</body></html>"""


@public.get("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe_page(token: str, settings: SettingsDep) -> HTMLResponse:
    target = read_token(settings, token)
    if target is None:
        return HTMLResponse(
            _PAGE.format(title="Link not valid", body="<p>This unsubscribe link is not valid.</p>"),
            400,
        )
    email = html_lib.escape(target.email)
    body = (f"<p>Stop all emails to <b>{email}</b>? You will not be contacted again.</p>"
            f'<form method="post"><button type="submit">Unsubscribe</button></form>')  # fmt: skip
    return HTMLResponse(_PAGE.format(title="Unsubscribe", body=body))


@public.post("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
async def unsubscribe(
    token: str,
    session: SessionDep,
    settings: SettingsDep,
) -> HTMLResponse:
    target = read_token(settings, token)
    if target is None:
        return HTMLResponse(
            _PAGE.format(title="Link not valid", body="<p>This unsubscribe link is not valid.</p>"),
            400,
        )
    email = None if target.email.startswith("lead:") else target.email
    await suppress(session, email=email, business_id=target.business_id, reason="unsubscribe")
    await session.commit()
    return HTMLResponse(_PAGE.format(title="You are unsubscribed",
                                     body="<p>Done — you will not receive any more emails from us.</p>"))  # fmt: skip
