"""Outreach agent: sends approved proposals.

Every send passes the full policy gate first. Real sending waits for the recipient's business
hours; the Mailpit sandbox delivers immediately unless `sandbox_respect_send_window` is set.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from sqlalchemy import select

from app.db.models import (
    ApprovalStatus,
    Business,
    DeliveryStatus,
    Lead,
    LeadStatus,
    Proposal,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.engine import PolicyEngine
from app.services import activity, runtime_settings
from app.services.sending import acquire_lock, deliver, next_send_time, schedule

logger = logging.getLogger(__name__)
STATUS_FOR_CHECK = {
    "suppression": LeadStatus.SUPPRESSED,
    "duplicate_cooldown": LeadStatus.COOLDOWN,
}


class OutreachAgent:
    name: ClassVar[str] = "outreach"
    expensive: ClassVar[bool] = False

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        if lead is None or lead.business_id is None:
            return AgentOutcome(False, None, "missing lead")
        proposal = (
            await s.execute(
                select(Proposal)
                .where(Proposal.lead_id == lead.id)
                .order_by(Proposal.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if proposal is None:
            return AgentOutcome(False, None, "no proposal to send")
        if proposal.approval_status == ApprovalStatus.PENDING:
            return AgentOutcome(False, LeadStatus.AWAITING_APPROVAL, "waiting for approval")
        if proposal.approval_status != ApprovalStatus.APPROVED:
            return AgentOutcome(
                False, LeadStatus.REJECTED_BY_HUMAN, f"proposal {proposal.approval_status}"
            )
        if not proposal.to_email:
            return AgentOutcome(
                False, LeadStatus.AWAITING_APPROVAL, "manual route: a human sends this proposal"
            )

        sandbox = bool(getattr(ctx.adapters.email, "is_sandbox", False))
        verdict = await PolicyEngine(s, settings=ctx.settings).evaluate_outreach(
            lead,
            email=proposal.to_email,
            recipient_type=lead.recipient_type or "unknown",
            sandbox=sandbox,
        )
        if not verdict.allowed:
            first = verdict.failures[0]
            status = STATUS_FOR_CHECK.get(first.check)
            if first.check == "send_limits":
                return AgentOutcome(
                    False, LeadStatus.APPROVED, f"send limit reached — will retry: {first.reason}"
                )
            return AgentOutcome(
                False, status or LeadStatus.NEEDS_REVIEW, f"policy blocked sending: {first.reason}"
            )

        if not await acquire_lock(s, lead):
            return AgentOutcome(
                False,
                LeadStatus.DUPLICATE,
                "another lead for this business holds the outreach lock",
            )

        rs = await runtime_settings.load(s)
        now = datetime.now(UTC)
        # A Gmail draft or compose link waits for a human, who chooses when to press Send.
        hands_off = bool(getattr(ctx.adapters.email, "hands_off", False))
        when = next_send_time(now, lead.timezone, lead.country, rs.sending_window)
        if when > now and not hands_off and (not sandbox or rs.sandbox_respect_send_window):
            await schedule(
                s, proposal=proposal, to=proposal.to_email, at=when, subject=proposal.subject
            )
            return AgentOutcome(
                False,
                LeadStatus.APPROVED,
                f"scheduled for {when:%Y-%m-%d %H:%M} UTC (recipient business hours)",
            )

        msg = await deliver(
            s, ctx.settings, ctx.adapters, lead=lead, proposal=proposal, to=proposal.to_email,
            subject=proposal.subject, html=proposal.body_html, text=proposal.body_text,
            attachments=proposal.attachments,
        )  # fmt: skip
        if msg.delivery_status == DeliveryStatus.DRAFTED:
            where = "a Gmail compose link" if msg.provider_id and msg.provider_id.startswith(
                "compose:") else "Gmail Drafts"  # fmt: skip
            await activity.emit(s, "email_drafted", f"Proposal for {proposal.to_email} is ready "
                                f"in {where}: press Send, then Mark as sent", lead_id=lead.id,
                                agent=self.name)  # fmt: skip
            return AgentOutcome(True, LeadStatus.AWAITING_SEND, f"ready in {where} for "
                                f"{proposal.to_email}; waiting for Mark as sent",
                                {"message_id": str(msg.id)})  # fmt: skip
        business = await s.get(Business, lead.business_id)
        if business is not None:
            business.cooldown_until = now + timedelta(days=rs.cooldown_days)
        note = " (sandbox: captured by Mailpit, not really sent)" if msg.sandbox else ""
        if msg.sandbox and when > now:
            note += f"; outside business hours — would have waited until {when:%Y-%m-%d %H:%M} UTC"
        await activity.emit(
            s,
            "email_sent",
            f"Proposal sent to {proposal.to_email}{note}",
            lead_id=lead.id,
            agent=self.name,
        )
        return AgentOutcome(True, LeadStatus.SENT, f"sent to {proposal.to_email}{note}",
                            {"message_id": str(msg.id), "sandbox": msg.sandbox})  # fmt: skip


register(OutreachAgent())
