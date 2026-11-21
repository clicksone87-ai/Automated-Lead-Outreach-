"""Sending: business-hours windows, idempotent message records and unsubscribe headers.

This module decides when and how to send, never whether; that is the policy gate's job.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import holidays
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.email import EmailAttachment, OutgoingEmail
from app.adapters.registry import Adapters
from app.config import Settings
from app.db.models import (
    Business,
    DeliveryStatus,
    Lead,
    LeadStatus,
    Message,
    OutreachLock,
    Proposal,
)
from app.orchestrator import quota
from app.services import activity, runtime_settings
from app.services import attachments as attachment_policy
from app.services.email_links import assert_no_local_links
from app.services.runtime_settings import SendingWindow
from app.services.unsubscribe import make_token, unsubscribe_mailto, unsubscribe_url

GMAIL_ADAPTERS = ("gmail", "gmail_compose")
logger = logging.getLogger(__name__)

DEFAULT_WINDOW = SendingWindow()


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_business_time(
    at: datetime, tz_name: str | None, country: str | None, window: SendingWindow = DEFAULT_WINDOW
) -> bool:
    local = at.astimezone(_tz(tz_name))
    if local.weekday() not in window.weekdays:
        return False
    if not (
        time(window.start_hour)
        <= local.time()
        < (time(window.end_hour) if window.end_hour < 24 else time.max)
    ):
        return False
    if not window.respect_holidays:
        return True
    try:
        return local.date() not in holidays.country_holidays(
            (country or "US").upper().replace("UK", "GB")
        )
    except NotImplementedError:
        return True


def next_send_time(
    now: datetime, tz_name: str | None, country: str | None, window: SendingWindow = DEFAULT_WINDOW
) -> datetime:
    """Now if inside the recipient's business hours, else the next opening time (UTC)."""
    if is_business_time(now, tz_name, country, window):
        return now
    tz = _tz(tz_name)
    local = now.astimezone(tz)
    opening = time(window.start_hour)
    day = local.date() if local.time() < opening else local.date() + timedelta(days=1)
    for _ in range(21):
        candidate = datetime.combine(day, opening, tzinfo=tz) + timedelta(minutes=7)
        if is_business_time(candidate, tz_name, country, window):
            return candidate.astimezone(UTC)
        day += timedelta(days=1)
    return now + timedelta(days=1)


def outgoing(
    settings: Settings,
    adapters: Adapters,
    *,
    lead: Lead,
    to: str,
    subject: str,
    html: str,
    text: str,
    attachments: list[dict[str, str]],
    idempotency_key: str,
    in_reply_to: str | None = None,
) -> OutgoingEmail:
    """Build the email. Raises LocalLinkError if any part would point at this machine."""
    token = make_token(settings, lead.business_id, to)
    url = unsubscribe_url(settings, token)
    mailto = unsubscribe_mailto(settings)
    # RFC 8058 one-click needs a public URL; without one only the mailto form is offered.
    headers = {"List-Unsubscribe": f"<{url}>, <{mailto}>" if url else f"<{mailto}>"}
    if url:
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    headers["X-Lead-Id"] = str(lead.id)
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to
    # Proposals are fitted when drafted; this re-check covers older records with the same policy.
    fitted = attachment_policy.fit(attachment_policy.load(adapters.storage, attachments))
    if fitted.changed:
        logger.warning("attachments for lead %s resized at send time: %s", lead.id, fitted.actions)
    files = [
        EmailAttachment(filename=i.filename, content=i.data, content_type=i.content_type)
        for i in fitted.items
    ]
    email = OutgoingEmail(
        from_address=settings.from_address,
        to=to,
        subject=subject,
        html=html,
        text=text,
        reply_to=settings.reply_address,
        headers=headers,
        attachments=files,
        idempotency_key=idempotency_key,
        tags={"lead": str(lead.id)},
    )
    assert_no_local_links(email)
    return email


async def acquire_lock(session: AsyncSession, lead: Lead) -> bool:
    """One active outreach per business. Returns True if this lead holds the lock."""
    assert lead.business_id is not None
    await session.execute(
        insert(OutreachLock)
        .values(business_id=lead.business_id, lead_id=lead.id)
        .on_conflict_do_nothing(index_elements=[OutreachLock.business_id])
    )
    holder = (
        await session.execute(
            select(OutreachLock.lead_id).where(OutreachLock.business_id == lead.business_id)
        )
    ).scalar_one()
    return holder == lead.id


async def deliver(
    session: AsyncSession,
    settings: Settings,
    adapters: Adapters,
    *,
    lead: Lead,
    proposal: Proposal,
    to: str,
    subject: str,
    html: str,
    text: str,
    attachments: list[dict[str, str]],
    follow_up_number: int = 0,
    in_reply_to: str | None = None,
) -> Message:
    """Send now (idempotent): returns the Message, already sent if it was sent before."""
    key = f"{proposal.id}:{follow_up_number}"
    msg = (
        await session.execute(select(Message).where(Message.idempotency_key == key))
    ).scalar_one_or_none()
    if msg is not None and (
        msg.sent_at is not None or msg.delivery_status == DeliveryStatus.DRAFTED
    ):
        return msg
    if msg is None:
        msg = Message(
            proposal_id=proposal.id,
            to_email=to,
            idempotency_key=key,
            follow_up_number=follow_up_number,
        )
        session.add(msg)
        await session.flush()
    email = outgoing(settings, adapters, lead=lead, to=to, subject=subject, html=html, text=text,
                     attachments=attachments, idempotency_key=key, in_reply_to=in_reply_to)  # fmt: skip
    if adapters.email.name in GMAIL_ADAPTERS:  # Gmail's own daily sending limit
        await quota.consume(session, "gmail", agent="outreach", lead_id=lead.id,
                            description=f"{adapters.email.name}: {subject}"[:200])  # fmt: skip
    provider_id = await adapters.email.send(email)
    msg.provider_id = provider_id
    msg.message_id_header = provider_id if provider_id.startswith("<") else None
    msg.subject = subject
    msg.body_text = text
    msg.sandbox = bool(getattr(adapters.email, "is_sandbox", False))
    msg.scheduled_for = None
    if getattr(adapters.email, "hands_off", False):
        msg.delivery_status = DeliveryStatus.DRAFTED
    else:
        msg.sent_at = datetime.now(UTC)
        msg.delivery_status = DeliveryStatus.SENT
    await session.flush()
    return msg


class MarkSentError(Exception):
    pass


async def mark_sent(session: AsyncSession, message_id: uuid.UUID, *, by: str) -> Message:
    """The human pressed Send in Gmail: record the send and start the cooldown."""
    msg = await session.get(Message, message_id)
    if msg is None:
        raise KeyError(message_id)
    if msg.delivery_status != DeliveryStatus.DRAFTED:
        raise MarkSentError(f"message is {msg.delivery_status}, not a draft waiting to be sent")
    now = datetime.now(UTC)
    msg.sent_at, msg.delivery_status = now, DeliveryStatus.SENT
    proposal = await session.get(Proposal, msg.proposal_id)
    lead = await session.get(Lead, proposal.lead_id) if proposal else None
    if lead is not None:
        if msg.follow_up_number == 0:
            lead.status, lead.status_reason = LeadStatus.SENT, f"sent from Gmail ({by})"
        business = await session.get(Business, lead.business_id) if lead.business_id else None
        if business is not None:
            rs = await runtime_settings.load(session)
            business.cooldown_until = now + timedelta(days=rs.cooldown_days)
    label = "Follow-up" if msg.follow_up_number else "Proposal"
    await activity.emit(session, "email_sent", f"{label} sent from Gmail to {msg.to_email} "
                        f"(marked by {by})", lead_id=lead.id if lead else None, agent="outreach")  # fmt: skip
    await session.flush()
    return msg


async def schedule(session: AsyncSession, *, proposal: Proposal, to: str, at: datetime,
                   follow_up_number: int = 0, subject: str | None = None) -> Message:  # fmt: skip
    key = f"{proposal.id}:{follow_up_number}"
    msg = (
        await session.execute(select(Message).where(Message.idempotency_key == key))
    ).scalar_one_or_none()
    if msg is None:
        msg = Message(proposal_id=proposal.id, to_email=to, idempotency_key=key,
                      follow_up_number=follow_up_number, subject=subject)  # fmt: skip
        session.add(msg)
    msg.scheduled_for = at
    msg.delivery_status = DeliveryStatus.SCHEDULED
    await session.flush()
    return msg
