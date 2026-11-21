"""Requirements agent: classifies the lead, scores buying intent and plans the next agents.

Leads below MIN_INTENT_SCORE stop here. LLM quotes and URLs that are not in the source text
are discarded.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from sqlalchemy import select

from app.adapters.llm import LLMRequest
from app.agents.discovery.base import detect_services, looks_like_request
from app.agents.requirements.schemas import Brief, IntentBreakdown, RequirementsLLM
from app.agents.requirements.scoring import combine, rule_signals
from app.db.models import Brief as BriefRow
from app.db.models import Business, Evidence, Lead, LeadStatus, TriggerType
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.country_rules import get_rules
from app.policy.engine import PolicyEngine
from app.services import runtime_settings
from app.services.dedup import BusinessCandidate, normalize_domain, resolve_business
from app.services.locale import timezone_for
from app.services.not_useful import NotUsefulReason
from app.services.not_useful_leads import stop

SYSTEM = (
    "You qualify inbound sales leads for a small agency that sells logo design, new websites, "
    "website redesigns and SEO to small businesses. Be strict: discussion, advice-seeking, "
    "people offering their own services, and vague wishes are NOT buying intent. Extract only "
    "facts present in the text; use null when something is not stated."
)
TASK = (
    "Classify the request in DATA. Decide whether the author wants to buy one of our services "
    "for their own business soon, which services, and extract stated facts. evidence_quotes "
    "must be short exact quotes copied from the text."
)

TRIGGER_DEFAULT_SERVICES: dict[str, list[str]] = {
    TriggerType.NEW_BUSINESS: ["website", "logo"],
    TriggerType.NO_WEBSITE: ["website"],
    TriggerType.BROKEN_OR_OUTDATED_SITE: ["redesign", "seo"],
    TriggerType.PROFILE_WITHOUT_SITE: ["website"],
}
ACTIVE_STATUSES = {
    LeadStatus.QUALIFIED,
    LeadStatus.CONTACT_FOUND,
    LeadStatus.AUDITED,
    LeadStatus.BRANDED,
    LeadStatus.BUILT,
    LeadStatus.QA_PASSED,
    LeadStatus.NEEDS_REVIEW,
    LeadStatus.AWAITING_APPROVAL,
    LeadStatus.APPROVED,
    LeadStatus.AWAITING_SEND,
    LeadStatus.SENT,
    LeadStatus.REPLIED,
}
_LTD = re.compile(r"\b(ltd|limited|llc|inc|plc|gmbh|corp|corporation|s\.?a\.?r\.?l)\b\.?", re.I)
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def verified_quotes(quotes: list[str], source: str) -> list[str]:
    haystack = _norm(source)
    return [q for q in quotes if len(q.strip()) >= 8 and _norm(q.strip(" \"'“”…")) in haystack]


def agents_to_run(services: list[str], has_site: bool, trigger: str) -> list[str]:
    run = ["contact_enrichment"]
    if has_site and (
        {"redesign", "seo"} & set(services) or trigger == TriggerType.BROKEN_OR_OUTDATED_SITE
    ):
        run.append("audit")
    if {"logo", "website", "redesign"} & set(services):
        run.append("branding")
    if {"website", "redesign"} & set(services):
        run.append("website_builder")
    if "branding" in run or "website_builder" in run:
        run.append("quality_check")
    return [*run, "proposal", "outreach"]


def recipient_type_for(business: Business | None, llm_value: str | None) -> str:
    if business is not None:
        if business.registry_source in ("companies_house",) or (
            business.registry_source or ""
        ).startswith("opencorporates"):
            return "corporate_entity"
        if _LTD.search(business.name):
            return "corporate_entity"
    return llm_value or "unknown"


@dataclass(slots=True)
class _Classification:
    llm: RequirementsLLM | None = None
    llm_score: int | None = None
    quotes: list[str] = field(default_factory=list)
    website: str | None = None
    assumptions: list[str] = field(default_factory=list)


class RequirementsAgent:
    name: ClassVar[str] = "requirements"
    expensive: ClassVar[bool] = False

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        if lead is None:
            return AgentOutcome(proceed=False, reason="lead not found")
        business = await s.get(Business, lead.business_id) if lead.business_id else None
        rs = await runtime_settings.load(s)
        text = "\n".join(filter(None, [lead.title, lead.raw_text]))

        cls = await self._classify(ctx, lead, business, text)
        if business is None:
            business = await self._resolve_business(ctx, lead, cls.llm, cls.website)
            lead.business_id = business.id
        elif cls.website and not business.domain:
            business.domain = normalize_domain(cls.website)
            business.has_website = business.domain is not None or business.has_website

        brief = await self._build_brief(
            ctx, lead, business, text=text, cls=cls, weights=rs.scoring_weights
        )
        row = await s.get(BriefRow, lead.id)
        if row is None:
            s.add(BriefRow(lead_id=lead.id, data=brief.model_dump()))
        else:
            row.data = brief.model_dump()
        lead.service_needed = list(brief.services)
        lead.intent_score = brief.intent.combined
        lead.country, lead.language, lead.timezone = brief.country, brief.language, brief.timezone
        lead.recipient_type = brief.recipient_type
        await s.flush()

        output: dict[str, object] = {
            "intent": brief.intent.combined,
            "services": list(brief.services),
            "agents_to_run": brief.agents_to_run,
        }
        stop = await self._gates(ctx, lead, brief, output)
        if stop is not None:
            return stop
        return AgentOutcome(
            True,
            LeadStatus.QUALIFIED,
            f"qualified: intent {brief.intent.combined}, services {', '.join(brief.services)}",
            dict(output),
            paid=cls.llm is not None,
        )

    async def _classify(
        self, ctx: AgentContext, lead: Lead, business: Business | None, text: str
    ) -> _Classification:
        """LLM classification — explicit requests only (proactive triggers cost nothing)."""
        out = _Classification()
        if lead.trigger_type != TriggerType.EXPLICIT_REQUEST or not text.strip():
            return out
        if (lead.language or "en") == "en" and not looks_like_request(text):
            # Free-tier guard (English only — the patterns are English): a post with no request
            # for a logo/website/redesign/SEO is scored by rules with zero intent and never
            # reaches the rate-limited LLM. Other languages always go to the LLM.
            out.llm_score = 0
            out.assumptions.append(
                "pre-filter: no request for a logo, website, redesign or SEO found; LLM not called"
            )
            return out
        stored = (lead.pipeline_state.get("classification") or {}).get("llm")
        if stored:
            # Classified in a fast-lane batch (or from the text cache): no LLM call here.
            return self._apply(out, RequirementsLLM.model_validate(stored), text)
        llm = await ctx.adapters.llm.structured(
            ctx.session,
            LLMRequest(
                agent=self.name,
                system=SYSTEM,
                task=TASK,
                lead_id=lead.id,
                untrusted={"title": lead.title or "", "text": lead.raw_text or ""},
                max_output_tokens=700,
                mock_context={"business_name": business.name if business else None},
            ),
            RequirementsLLM,
        )
        if ctx.adapters.llm.is_mock:
            used = set(lead.pipeline_state.get("mock_adapters_used", []))
            lead.pipeline_state = {
                **lead.pipeline_state,
                "mock_adapters_used": sorted(used | {"openai"}),
            }
        return self._apply(out, llm, text)

    @staticmethod
    def _apply(out: _Classification, llm: RequirementsLLM, text: str) -> _Classification:
        """Keep only what the source text supports; cap scores without verifiable quotes."""
        out.llm = llm
        out.quotes = verified_quotes(llm.evidence_quotes, text)
        dropped = len(llm.evidence_quotes) - len(out.quotes)
        if dropped:
            out.assumptions.append(f"discarded {dropped} quote(s) not found in the source text")
        out.llm_score = llm.intent_score
        if not llm.is_buying_intent:
            out.llm_score = min(out.llm_score, 25)
        elif not out.quotes:
            out.llm_score = min(out.llm_score, 35)
            out.assumptions.append("intent capped: no verifiable quote")
        if llm.existing_website and llm.existing_website.lower() in text.lower():
            out.website = llm.existing_website
        elif llm.existing_website:
            out.assumptions.append("discarded website URL not present in the text")
        return out

    async def _build_brief(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        *,
        text: str,
        cls: _Classification,
        weights: dict[str, float],
    ) -> Brief:
        llm = cls.llm
        first_evidence = (
            (
                await ctx.session.execute(
                    select(Evidence).where(Evidence.lead_id == lead.id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        posted_raw = (first_evidence.data or {}).get("posted_at") if first_evidence else None
        posted_at = datetime.fromisoformat(posted_raw) if isinstance(posted_raw, str) else None
        signals = rule_signals(
            lead.trigger_type,
            text,
            posted_at=posted_at,
            business_known=bool(llm and llm.business_name) or business.registry_number is not None,
            weights=weights,
        )
        combined = combine(cls.llm_score, signals, weights)
        services = sorted(
            set(
                (llm.services if llm else [])
                or (lead.service_needed or [])
                or detect_services(text)
                or TRIGGER_DEFAULT_SERVICES.get(lead.trigger_type, ["website"])
            )
        )
        country = lead.country or (llm.country if llm else None) or business.country
        language = lead.language or (llm.language if llm else None) or business.language
        location = (llm.location if llm else None) or business.address
        tz = lead.timezone or timezone_for(country, " ".join(filter(None, [location, text])))
        assumptions = list(cls.assumptions)
        if country == "US" and tz is None:
            tz = "America/New_York"
            assumptions.append("US state unknown: sending window assumes America/New_York")
        has_site = bool(business.domain) or bool(business.has_website)
        return Brief.model_validate(
            {
                "services": services,
                "business_name": business.name,
                "business_type": (llm.business_type if llm else None) or business.industry,
                "location": location,
                "country": country,
                "language": language,
                "timezone": tz,
                "existing_website": f"https://{business.domain}"
                if business.domain
                else cls.website,
                "recipient_type": recipient_type_for(business, llm.recipient_type if llm else None),
                "budget": llm.budget if llm else None,
                "timeline": llm.timeline if llm else None,
                "available_info": llm.available_info if llm else [],
                "evidence_quotes": cls.quotes,
                "intent": IntentBreakdown(
                    llm_score=cls.llm_score,
                    rule_score=max(0, min(100, sum(signals.values()))),
                    combined=combined,
                    signals=signals,
                ),
                "agents_to_run": agents_to_run(services, has_site, lead.trigger_type),
                "assumptions": assumptions,
                "used_llm": llm is not None,
            }
        )

    async def _gates(
        self, ctx: AgentContext, lead: Lead, brief: Brief, output: dict[str, object]
    ) -> AgentOutcome | None:
        """Cheapest checks first; every stop is explained. None = lead continues."""
        s = ctx.session
        rs = await runtime_settings.load(s)
        out = dict(output)
        language, country = brief.language, brief.country
        low_intent = brief.intent.combined < rs.min_intent_score
        intent_reason = f"intent {brief.intent.combined} < minimum {rs.min_intent_score}"
        if not language or language not in rs.enabled_languages:
            reason = f"language {language or 'unknown'} not enabled"
            return AgentOutcome(False, LeadStatus.PARKED_LANGUAGE, reason, out)
        rules = get_rules()
        rule = rules.get(country)
        # Worldwide (ALL_COUNTRIES_OUTREACH_ENABLED): every country except the excluded markets;
        # otherwise only the target countries chosen in Settings.
        targeted = ctx.settings.all_countries_outreach_enabled or country in rs.target_countries
        if (
            not country
            or rules.is_excluded(country)
            or not targeted
            or rule is None
            or not rule.can_draft
        ):
            if not country:
                if low_intent:  # nothing to wait for: an unknown-country lead with no intent
                    return stop(lead, NotUsefulReason.LOW_INTENT, intent_reason, out)
                why = "country unknown"
            elif rules.is_excluded(country):
                return stop(
                    lead, NotUsefulReason.EXCLUDED_MARKET, f"{country} is an excluded market", out
                )
            elif not targeted:
                why = f"{country} not a target country"
            else:
                why = f"{country} has no outreach rules"
            return AgentOutcome(False, LeadStatus.PARKED_COUNTRY, why, out)

        verdict = await PolicyEngine(s).evaluate_drafting(lead)
        for failure in verdict.failures:
            if failure.check == "suppression":
                return AgentOutcome(False, LeadStatus.SUPPRESSED, failure.reason, out)
            if failure.check == "duplicate_cooldown":
                cooldown = "cooldown" in failure.reason
                status = LeadStatus.COOLDOWN if cooldown else LeadStatus.DUPLICATE
                return AgentOutcome(False, status, failure.reason, out)
        other_active = (
            await s.execute(
                select(Lead.id)
                .where(
                    Lead.business_id == lead.business_id,
                    Lead.id != lead.id,
                    Lead.status.in_([str(x) for x in ACTIVE_STATUSES]),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if other_active:
            reason = f"business already in pipeline via lead {other_active}"
            return AgentOutcome(False, LeadStatus.DUPLICATE, reason, out)
        if not verdict.allowed:
            return AgentOutcome(False, LeadStatus.PARKED_COUNTRY, verdict.failures[0].reason, out)
        if low_intent:
            return stop(lead, NotUsefulReason.LOW_INTENT, intent_reason, out)
        return None

    async def _resolve_business(
        self, ctx: AgentContext, lead: Lead, llm: RequirementsLLM | None, website: str | None
    ) -> Business:
        s = ctx.session
        if llm and llm.business_name:
            match = await resolve_business(
                s,
                BusinessCandidate(
                    name=llm.business_name,
                    domain=website,
                    country=lead.country or llm.country,
                    language=lead.language,
                    timezone=lead.timezone,
                ),
            )
            return match.business
        evidence = (
            (await s.execute(select(Evidence).where(Evidence.lead_id == lead.id).limit(1)))
            .scalars()
            .first()
        )
        author = (evidence.data or {}).get("author") if evidence else None
        handle = str(author or lead.source_item_id)[:100]
        # Human-readable label: the author, else the page's site, else the title (the hashed
        # item id stays only as the identity key below).
        site = normalize_domain(lead.source_url) if lead.source_url else None
        label = str(author or site or (lead.title or "")[:60] or handle)
        # A person on a platform: exact identity by (source, handle), never fuzzy-merged.
        match = await resolve_business(
            s,
            BusinessCandidate(
                name=f"{label} ({lead.source})",
                registry_source=f"handle:{lead.source}",
                registry_number=handle,
                domain=website,
                country=lead.country,
                language=lead.language,
                timezone=lead.timezone,
                allow_fuzzy=False,
            ),
        )
        return match.business


register(RequirementsAgent())
