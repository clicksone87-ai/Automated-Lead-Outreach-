"""The "Not useful" bucket: leads that will never be worked, each with one reason.

They leave every working view and count; the dashboard's "Not useful" tab lists them with the
reason. `mark` is the only way a lead enters the bucket, so every path records the same fields.
"""

from __future__ import annotations

from enum import StrEnum

from app.db.models import Lead, LeadStatus


class NotUsefulReason(StrEnum):
    NO_CONTACT = "no_contact"
    OLDER_THAN_8H = "older_than_8h"
    FREELANCE = "freelance"
    EXCLUDED_MARKET = "excluded_market"
    LOW_INTENT = "low_intent"


LABELS: dict[NotUsefulReason, str] = {
    NotUsefulReason.NO_CONTACT: "no contact route",
    NotUsefulReason.OLDER_THAN_8H: "older than 8 h",
    NotUsefulReason.FREELANCE: "freelance platform",
    NotUsefulReason.EXCLUDED_MARKET: "excluded market",
    NotUsefulReason.LOW_INTENT: "low intent",
}


def mark(lead: Lead, reason: NotUsefulReason, detail: str | None = None) -> None:
    """Move a lead to "Not useful" with its reason (idempotent)."""
    lead.status = LeadStatus.NOT_USEFUL
    lead.not_useful_reason = reason.value
    lead.status_reason = f"not useful: {LABELS[reason]}" + (f" ({detail})" if detail else "")
