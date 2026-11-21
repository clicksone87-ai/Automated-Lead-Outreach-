"""Prefilter agent: the first fast-lane step. Plain rules, no LLM, no network.

Rejects stale posts, sellers, posts that ask for nothing, repeated post text and leads from
excluded markets (or untargeted countries when worldwide outreach is off), before any LLM
quota is spent. Social posts past the 8-hour rule and freelance-platform listings go to "Not
useful" with their reason. Proactive triggers (new business, outdated site …) carry no request
text and only get the country check.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import select

from app.agents.discovery.freshness import is_social_url
from app.agents.prefilter.rules import BOARD_URL, check_text, stale_reason
from app.db.models import Evidence, EvidenceKind, Lead, LeadStatus, TriggerType
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.country_rules import get_rules
from app.services import runtime_settings
from app.services.families import SOCIAL, family_for
from app.services.not_useful import NotUsefulReason
from app.services.not_useful_leads import stop


class PrefilterAgent:
    name: ClassVar[str] = "prefilter"
    expensive: ClassVar[bool] = False

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        if lead is None:
            return AgentOutcome(False, None, "lead not found")
        rs = await runtime_settings.load(s)

        if lead.country and get_rules().is_excluded(lead.country):
            return self._not_useful(
                lead, NotUsefulReason.EXCLUDED_MARKET, f"{lead.country} is an excluded market"
            )
        if (
            lead.country
            and not ctx.settings.all_countries_outreach_enabled
            and lead.country not in rs.target_countries
        ):
            return self._stop(
                lead, LeadStatus.PARKED_COUNTRY, f"{lead.country} not a target country"
            )
        if lead.trigger_type != TriggerType.EXPLICIT_REQUEST:
            return AgentOutcome(
                True, None, "proactive trigger: scored by rules", {"needs_llm": False}
            )

        if BOARD_URL.search(lead.source_url):
            return self._not_useful(lead, NotUsefulReason.FREELANCE, "freelance-platform listing")

        family = lead.source_family or family_for(lead.source)
        if is_social_url(lead.source_url):
            family = SOCIAL
        stale = stale_reason(
            lead.posted_at,
            family=family,
            now=datetime.now(UTC),
            # SOCIAL_MAX_POST_AGE_HOURS is the owner's rule for social posts (8 h); it wins
            # over any older value saved in the settings table.
            max_age_hours={
                **rs.max_post_age_hours,
                "social": ctx.settings.social_max_post_age_hours,
            },
        )
        if stale and family == SOCIAL:
            return self._not_useful(lead, NotUsefulReason.OLDER_THAN_8H, stale)
        if stale:
            return self._not_useful(lead, NotUsefulReason.OLDER_THAN_8H, stale)

        text = "\n".join(filter(None, [lead.title, lead.raw_text]))
        verdict = check_text(text, lead.language)
        if not verdict.passed:
            return self._not_useful(lead, NotUsefulReason.LOW_INTENT, verdict.reason)

        if await self._repeated_text(ctx, lead):
            return self._stop(
                lead, LeadStatus.DUPLICATE, "same post text already came in as another lead"
            )
        return AgentOutcome(True, None, "prefilter passed", {"needs_llm": verdict.needs_llm})

    @staticmethod
    def _stop(lead: Lead, status: LeadStatus, reason: str) -> AgentOutcome:
        lead.prefilter_reason = reason
        return AgentOutcome(False, status, f"prefilter: {reason}", {"rule": reason})

    @staticmethod
    def _not_useful(lead: Lead, why: NotUsefulReason, reason: str) -> AgentOutcome:
        lead.prefilter_reason = reason
        if why != NotUsefulReason.EXCLUDED_MARKET:
            lead.intent_score = 0  # counted as low intent, never as "not scored yet"
        return stop(lead, why, f"prefilter: {reason}", {"rule": reason})

    @staticmethod
    async def _repeated_text(ctx: AgentContext, lead: Lead) -> bool:
        """Cross-posted or re-shared text: the earliest lead keeps it, later copies stop here."""
        mine = select(Evidence.content_hash).where(
            Evidence.lead_id == lead.id, Evidence.kind == EvidenceKind.POST
        )
        earlier = (
            await ctx.session.execute(
                select(Evidence.lead_id)
                .join(Lead, Lead.id == Evidence.lead_id)
                .where(
                    Evidence.kind == EvidenceKind.POST,
                    Evidence.content_hash.in_(mine),
                    Evidence.lead_id != lead.id,
                    Lead.created_at < lead.created_at,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return earlier is not None


register(PrefilterAgent())
