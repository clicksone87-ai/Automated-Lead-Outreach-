"""One-off clean-up of stored leads that the current rules would never have created.

Social posts that were older than SOCIAL_MAX_POST_AGE_HOURS when found (created_at minus the
post's own time), or that carry no post time, move to "Not useful: older than 8 h"; leads from
freelance platforms (the retired Freelancer.com and alert-email sources, or a job-board listing
URL from any source) move to "Not useful: freelance". Idempotent: leads already in the bucket
are left alone, so a second run changes nothing. Leads a human has already acted on (draft in
Gmail, sent, replied, won, lost, closed) keep their status and are only counted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.discovery.freshness import is_social_post, max_post_age
from app.agents.prefilter.rules import BOARD_URL
from app.config import Settings
from app.db.models import Lead, LeadStatus
from app.services import not_useful
from app.services.not_useful import NotUsefulReason

FREELANCE_SOURCES = frozenset({"freelancer", "alert_emails"})
#: A person already acted on these; moving them would hide real outreach history.
ACTED_ON = frozenset(
    {
        LeadStatus.AWAITING_SEND,
        LeadStatus.SENT,
        LeadStatus.REPLIED,
        LeadStatus.WON,
        LeadStatus.LOST,
        LeadStatus.CLOSED,
    }
)


@dataclass(slots=True)
class CleanupResult:
    scanned: int = 0
    older_than_8h: int = 0
    freelance: int = 0
    kept_acted_on: int = 0
    relabelled: int = 0  # already "Not useful", now under the rule that applies first
    by_source: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "scanned": self.scanned,
            "older_than_8h": self.older_than_8h,
            "freelance": self.freelance,
            "kept_acted_on": self.kept_acted_on,
            "relabelled": self.relabelled,
            "by_source": dict(self.by_source),
        }


def verdict(lead: Lead, settings: Settings | None = None) -> tuple[NotUsefulReason, str] | None:
    """Why the lead belongs in "Not useful" under the current rules, or None."""
    if lead.source in FREELANCE_SOURCES or BOARD_URL.search(lead.source_url or ""):
        return NotUsefulReason.FREELANCE, f"from {lead.source}"
    if not is_social_post(lead.source, lead.trigger_type, lead.source_url):
        return None
    if lead.posted_at is None:
        return NotUsefulReason.OLDER_THAN_8H, "post has no timestamp"
    age = lead.created_at - lead.posted_at
    if age > max_post_age(settings):
        return NotUsefulReason.OLDER_THAN_8H, f"post was {age.total_seconds() / 3600:.0f} h old"
    return None


async def clean_leads(
    session: AsyncSession, settings: Settings | None = None, *, dry_run: bool = False
) -> CleanupResult:
    """Move every matching lead to "Not useful"; with `dry_run`, only count them."""
    result = CleanupResult()
    # Age and freelance are source rules checked before wording or contact: a stale post that
    # was also filed as "low intent" or "no contact" belongs under its age.
    weaker = [NotUsefulReason.LOW_INTENT.value, NotUsefulReason.NO_CONTACT.value]
    rows = await session.scalars(
        select(Lead)
        .where(
            or_(
                Lead.status != LeadStatus.NOT_USEFUL,
                Lead.not_useful_reason.is_(None),
                Lead.not_useful_reason.in_(weaker),
            )
        )
        .order_by(Lead.created_at)
    )
    for lead in rows:
        result.scanned += 1
        found = verdict(lead, settings)
        if found is None:
            continue
        reason, detail = found
        if lead.status in ACTED_ON:
            result.kept_acted_on += 1
            continue
        if lead.status == LeadStatus.NOT_USEFUL:
            if lead.not_useful_reason == reason.value:
                continue
            result.relabelled += 1
        if reason == NotUsefulReason.FREELANCE:
            result.freelance += 1
        else:
            result.older_than_8h += 1
        result.by_source[f"{lead.source}:{reason.value}"] += 1
        if not dry_run:
            not_useful.mark(lead, reason, detail)
    if not dry_run:
        await session.flush()
    return result
