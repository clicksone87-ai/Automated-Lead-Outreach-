"""Proactive triggers as discovery sources.

`site_health` finds broken or outdated sites of known businesses; `no_website_check` confirms
businesses that have no website (or only a Places profile).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import or_, select

from app.adapters.base import AdapterUnavailableError
from app.adapters.fetcher import FetchError, RobotsDisallowedError
from app.agents.discovery.base import RawBusiness, RawLead, SourceContext
from app.agents.discovery.proactive_triggers.signals import (
    classify_site,
    domain_candidates,
    page_mentions_business,
)
from app.db.models import Business, EvidenceKind, Lead, TriggerType

logger = logging.getLogger(__name__)


def _raw_business(b: Business, **overrides: Any) -> RawBusiness:
    data: dict[str, Any] = {
        "name": b.name,
        "website": f"https://{b.domain}" if b.domain else None,
        "phone": b.phone,
        "address": b.address,
        "country_hint": b.country,
        "registry_number": b.registry_number,
        "registry_source": b.registry_source,
        "industry": b.industry,
        "has_website": b.has_website,
    }
    return RawBusiness(**(data | overrides))


async def _has_lead(ctx: SourceContext, business_id: Any, trigger: str | None = None) -> bool:
    stmt = select(Lead.id).where(Lead.business_id == business_id)
    if trigger:
        stmt = stmt.where(Lead.trigger_type == trigger)
    return (await ctx.session.execute(stmt.limit(1))).scalar_one_or_none() is not None


class SiteHealthSource:
    key: ClassVar[str] = "site_health"
    label: ClassVar[str] = "Trigger: broken or outdated websites"
    description: ClassVar[str] = (
        "Re-checks known business websites for errors, missing HTTPS, no mobile layout and "
        "old-design signals."
    )
    integration: ClassVar[str] = "web"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 120
    constraints: ClassVar[str] = "Homepage only, via the safe fetcher (robots.txt, rate limits)."
    default_config: ClassVar[dict[str, Any]] = {"per_run": 20, "recheck_days": 30}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        cutoff = datetime.now(UTC) - timedelta(days=int(ctx.config.get("recheck_days", 30)))
        rows = (
            (
                await ctx.session.execute(
                    select(Business)
                    .where(
                        Business.domain.is_not(None),
                        Business.industry == ctx.runtime.active_industry,
                        Business.country.in_(ctx.runtime.target_countries),
                        or_(Business.site_checked_at.is_(None), Business.site_checked_at < cutoff),
                    )
                    .order_by(Business.site_checked_at.asc().nulls_first())
                    .limit(int(ctx.config.get("per_run", 20)))
                )
            )
            .scalars()
            .all()
        )
        leads: list[RawLead] = []
        for b in rows:
            url = f"https://{b.domain}/"
            try:
                page = await ctx.adapters.fetcher.get(url)
                health = classify_site(page.status_code, page.text, page.final_url)
                final_url = page.final_url
            except RobotsDisallowedError:
                b.site_checked_at = datetime.now(UTC)  # site opted out of crawling: respect it
                continue
            except FetchError:
                health, final_url = classify_site(None, None, None), url
            b.site_status, b.site_checked_at = health.status, datetime.now(UTC)
            if health.status in ("ok", "protected") or await _has_lead(
                ctx, b.id, TriggerType.BROKEN_OR_OUTDATED_SITE
            ):
                continue
            leads.append(
                RawLead(
                    source_item_id=f"site:{b.domain}:{datetime.now(UTC):%Y%m}",
                    source_url=final_url,
                    trigger_type=TriggerType.BROKEN_OR_OUTDATED_SITE,
                    title=f"{b.name} — website {health.status}",
                    text=f"{b.name} ({b.domain}): {health.status}; signals: {', '.join(health.signals)}.",
                    business=_raw_business(b),
                    evidence_kind=EvidenceKind.METRIC,
                    evidence_data={"site_status": health.status, "signals": health.signals},
                )
            )
        return leads


class NoWebsiteSource:
    key: ClassVar[str] = "no_website_check"
    label: ClassVar[str] = "Trigger: businesses without a website"
    description: ClassVar[str] = (
        "For businesses listed without a site, checks likely domains; optionally looks up the "
        "single business on Google Places to find a profile without a website."
    )
    integration: ClassVar[str] = "web"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 180
    constraints: ClassVar[str] = (
        "Domain check only confirms existence; Places is used for single-business lookups only "
        "(no bulk database)."
    )
    default_config: ClassVar[dict[str, Any]] = {"per_run": 10, "places_lookups": True}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        rows = (
            (
                await ctx.session.execute(
                    select(Business)
                    .where(
                        Business.domain.is_(None),
                        or_(Business.has_website.is_(None), Business.has_website.is_(False)),
                        Business.site_checked_at.is_(None),
                        Business.industry == ctx.runtime.active_industry,
                        Business.country.in_(ctx.runtime.target_countries),
                        Business.registry_source.is_(None)
                        | ~Business.registry_source.like("handle:%"),
                    )
                    .limit(int(ctx.config.get("per_run", 10)))
                )
            )
            .scalars()
            .all()
        )
        leads: list[RawLead] = []
        for b in rows:
            b.site_checked_at = datetime.now(UTC)
            found = await self._domain_check(ctx, b)
            if found:
                b.domain, b.has_website, b.site_status = found, True, None
                b.site_checked_at = None  # let site_health check it next
                continue
            b.has_website = False
            profile = await self._places(ctx, b) if ctx.config.get("places_lookups", True) else None
            if profile is not None and not profile.get("website"):
                if not await _has_lead(ctx, b.id, TriggerType.PROFILE_WITHOUT_SITE):
                    leads.append(
                        RawLead(
                            source_item_id=f"profile:{profile['place_id']}",
                            source_url=profile.get("maps_url")
                            or f"https://maps.google.com/?q={b.name}",
                            trigger_type=TriggerType.PROFILE_WITHOUT_SITE,
                            title=f"{b.name} — business profile without a website",
                            text=f"{b.name} has a business profile ({profile.get('rating_count') or 0} reviews) "
                            "but no website listed; no domain found for the name.",
                            business=_raw_business(b, has_website=False),
                            evidence_kind=EvidenceKind.METRIC,
                            evidence_data={"places": profile, "domain_check": "none found"},
                        )
                    )
                continue
            if not await _has_lead(ctx, b.id):
                leads.append(
                    RawLead(
                        source_item_id=f"nosite:{b.id}",
                        source_url=f"urn:no-website-check:{b.id}",
                        trigger_type=TriggerType.NO_WEBSITE,
                        title=f"{b.name} — no website found",
                        text=f"{b.name}: listed without a website and no domain found for the name.",
                        business=_raw_business(b, has_website=False),
                        evidence_kind=EvidenceKind.METRIC,
                        evidence_data={"domain_check": "none found"},
                    )
                )
        return leads

    @staticmethod
    async def _domain_check(ctx: SourceContext, b: Business) -> str | None:
        for domain in domain_candidates(b.name, b.country):
            try:
                page = await ctx.adapters.fetcher.get(f"https://{domain}/")
            except FetchError:
                continue
            if page.ok and page_mentions_business(page.text, b.name):
                return domain
        return None

    @staticmethod
    async def _places(ctx: SourceContext, b: Business) -> dict[str, Any] | None:
        query = ", ".join(filter(None, [b.name, b.address]))
        try:
            place = await ctx.adapters.places.lookup(
                ctx.session, query, agent="discovery", region=b.country
            )
        except AdapterUnavailableError:
            return None
        return place.model_dump() if place else None
