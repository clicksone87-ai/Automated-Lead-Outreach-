"""Competitor discovery shared by the Audit (comparison) and Branding (styles, logos) agents.

Uses the search adapter only (never scrapes result pages) with a "<trade> <city>" query and
returns candidate domains in result order; callers fetch them through the safe fetcher and
stop at their own limit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.adapters.search import SearchAdapter
from app.db.models import Business
from app.services.dedup import PLATFORM_DOMAINS, normalize_domain


async def competitor_domains(
    session: AsyncSession,
    search: SearchAdapter,
    business: Business,
    brief: dict[str, Any],
    *,
    agent: str,
    count: int = 10,
) -> list[str]:
    trade = brief.get("business_type") or (business.industry or "").replace("_", " ")
    city = (brief.get("location") or business.address or "").split(",")[0].strip()
    if not trade or not city:
        return []
    try:
        results = await search.search(
            session, f"{trade} {city}", agent=agent, country=business.country, count=count
        )
    except AdapterUnavailableError:
        return []
    if search.is_mock:
        return []  # mock search returns fixture forum URLs, not competitors
    seen = {business.domain}
    domains: list[str] = []
    for r in results:
        domain = normalize_domain(r.url)
        if not domain or domain in seen or domain in PLATFORM_DOMAINS:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains
