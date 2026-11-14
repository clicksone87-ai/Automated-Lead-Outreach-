"""Public business websites & directories (robots.txt, terms and rate limits respected).

Reads schema.org LocalBusiness data from directory listing pages you configure (only
directories whose terms permit it). Businesses without a website become `no_website` leads
(the domain check in the proactive trigger confirms before money is spent); businesses with a
website are stored for the broken/outdated-site trigger to scan.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, ClassVar

from app.adapters.fetcher import FetchError
from app.agents.discovery.base import RawLead, SourceContext
from app.agents.discovery.sources._html import jsonld_businesses
from app.db.models import EvidenceKind, TriggerType
from app.services.dedup import BusinessCandidate, resolve_business
from app.services.locale import guess_locale

logger = logging.getLogger(__name__)


class WebDirectorySource:
    key: ClassVar[str] = "web_directory"
    label: ClassVar[str] = "Public websites & directories"
    description: ClassVar[str] = (
        "Reads structured business listings (schema.org) from public directory pages you "
        "configure, through the safe fetcher."
    )
    integration: ClassVar[str] = "web"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 60
    constraints: ClassVar[str] = (
        "Only directories whose terms allow it; robots.txt, rate limits, no login walls."
    )
    default_config: ClassVar[dict[str, Any]] = {
        # Each entry: {"url": "...", "country": "GB", "industry": "home_services"}
        "listing_urls": [],
        "pages_per_run": 5,
    }

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        entries = ctx.config.get("listing_urls", [])
        if not entries:
            return []
        per_run = int(ctx.config.get("pages_per_run", 5))
        cursor = int(ctx.state.get("cursor", 0))
        batch = [entries[(cursor + i) % len(entries)] for i in range(min(per_run, len(entries)))]
        ctx.state["cursor"] = cursor + len(batch)

        leads: list[RawLead] = []
        for entry in batch:
            url = entry["url"] if isinstance(entry, dict) else str(entry)
            country = entry.get("country") if isinstance(entry, dict) else None
            industry = (
                entry.get("industry") if isinstance(entry, dict) else None
            ) or ctx.runtime.active_industry
            try:
                page = await ctx.adapters.fetcher.get(url)
            except FetchError as exc:
                logger.info("directory page %s skipped: %s", url, exc)
                continue
            if not page.ok:
                continue
            for biz in jsonld_businesses(page.text, page.final_url):
                biz.country_hint = biz.country_hint or country
                biz.industry = industry
                if biz.website:
                    # Store for the broken/outdated-site trigger; not a lead yet.
                    loc = guess_locale(
                        url=biz.website,
                        phone=biz.phone,
                        address=biz.address,
                        country_hint=biz.country_hint,
                    )
                    await resolve_business(
                        ctx.session,
                        BusinessCandidate(
                            name=biz.name,
                            domain=biz.website,
                            country=loc.country,
                            language=loc.language,
                            timezone=loc.timezone,
                            industry=industry,
                            address=biz.address,
                            phone=biz.phone,
                            has_website=True,
                        ),
                    )
                    continue
                ident = f"{biz.name}|{biz.address}|{biz.phone}"
                leads.append(
                    RawLead(
                        source_item_id=hashlib.sha256(ident.encode()).hexdigest()[:40],
                        source_url=page.final_url,
                        trigger_type=TriggerType.NO_WEBSITE,
                        title=f"{biz.name} — listed without a website",
                        text=f"{biz.name}\n{biz.address or ''}\nListed in directory without a website.",
                        business=biz,
                        evidence_kind=EvidenceKind.PAGE,
                        evidence_data={"directory": url, "listing": biz.model_dump()},
                    )
                )
        return leads
