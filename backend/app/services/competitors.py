"""Competitor discovery shared by the Audit and Branding agents.

Runs a "<trade> <city>" search and returns candidate domains in result order. Results are
cached for a week, and concurrent callers for the same query (the heavy lane runs Audit and
Branding side by side) share one search.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.adapters.search import SearchAdapter
from app.db.models import Business
from app.db.session import session_scope
from app.services import cache
from app.services.dedup import PLATFORM_DOMAINS, normalize_domain

CACHE_TTL = timedelta(days=7)
_LOCKS: dict[str, asyncio.Lock] = {}


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
    if search.is_mock:
        return []  # mock search returns fixture forum URLs, not competitors
    query = f"{trade} {city}"
    key = cache.make_key(
        "competitors", {"q": query.casefold(), "country": business.country, "n": count}
    )
    async with _LOCKS.setdefault(key, asyncio.Lock()):
        found = await cache.get(session, key)
        if found is None:
            try:
                results = await search.search(
                    session, query, agent=agent, country=business.country, count=count
                )
            except AdapterUnavailableError:
                return []
            found = {"domains": [d for r in results if (d := normalize_domain(r.url))]}
            async with session_scope() as own:  # committed now, so the sibling step sees it
                await cache.put(own, key, "competitors", found, ttl=CACHE_TTL)
    seen = {business.domain}
    domains: list[str] = []
    for domain in found.get("domains", []):
        if not domain or domain in seen or domain in PLATFORM_DOMAINS:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains
