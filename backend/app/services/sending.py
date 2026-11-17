"""Sending: business-hours windows, idempotent message records and unsubscribe headers.

This module decides when and how to send, never whether; that is the policy gate's job.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import holidays
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.email import EmailAttachment, OutgoingEmail
from app.adapters.registry import Adapters
from app.config import Settings
from app.db.models import DeliveryStatus, Lead, Message, OutreachLock, Proposal
from app.services.runtime_settings import SendingWindow
from app.services.unsubscribe import make_token, unsubscribe_url

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
    token = make_token(settings, lead.business_id, to)
    url = unsubscribe_url(settings, token)
    headers = {
        # RFC 8058 one-click unsubscribe + mailto fallback
        "List-Unsubscribe": f"<{url}>, <mailto:{settings.reply_address}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "X-Lead-Id": str(lead.id),
    }
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to
    files = []
    for a in attachments:
        try:
            files.append(
                EmailAttachment(
                    filename=a["filename"],
                    content=adapters.storage.read_bytes(a["path"]),
                    content_type=a.get("content_type", "application/octet-stream"),
                )
            )
        except (OSError, ValueError, KeyError):
            logger.warning("attachment missing for lead %s: %s", lead.id, a.get("path"))
    return OutgoingEmail(
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
    if msg is not None and msg.sent_at is not None:
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
    provider_id = await adapters.email.send(email)
    msg.provider_id = provider_id
    msg.message_id_header = provider_id if provider_id.startswith("<") else None
    msg.subject = subject
    msg.sent_at = datetime.now(UTC)
    msg.delivery_status = DeliveryStatus.SENT
    msg.sandbox = bool(getattr(adapters.email, "is_sandbox", False))
    msg.scheduled_for = None
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
