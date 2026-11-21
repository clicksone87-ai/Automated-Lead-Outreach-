"""Search API source: Brave Search queries, then fetch the result pages (never scrape Google).

A result that is a social post must prove it is fresh from the result's own date ("3 hours
ago", ISO time); an undated or older one is skipped before its page is fetched.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.adapters.fetcher import FetchError
from app.agents.discovery.base import (
    RawLead,
    SourceContext,
    industry_keywords,
    looks_like_request,
)
from app.agents.discovery.freshness import is_fresh_post, is_social_url, parse_result_age
from app.agents.discovery.sources._html import main_text, page_title
from app.db.models import EvidenceKind, TriggerType

logger = logging.getLogger(__name__)

QUERY_TEMPLATES = (
    '"looking for" "web designer" {kw}',
    '"need a website" {kw}',
    '"need a new website" {kw} business',
    '"website redesign" {kw} "looking for"',
    '"need a logo" {kw} business',
    '"recommend" "web designer" {kw}',
)


class SearchApiSource:
    key: ClassVar[str] = "search_api"
    label: ClassVar[str] = "Google indexed pages (Serper → Exa)"
    description: ClassVar[str] = (
        "Searches indexed pages for explicit requests via the Serper Google results API (Exa "
        "as fallback), then fetches each result page through the safe fetcher."
    )
    integration: ClassVar[str] = "serper"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 30
    constraints: ClassVar[str] = (
        "Licensed search APIs only (Serper, Exa) — Google result pages are never scraped."
    )
    default_config: ClassVar[dict[str, Any]] = {"queries_per_run": 3, "freshness": "pw"}

    def queries(self, ctx: SourceContext) -> list[tuple[str, str]]:
        kws = industry_keywords(ctx.runtime.active_industry)
        countries = ctx.runtime.target_countries or ["US"]
        return [
            (tpl.format(kw=kw), country)
            for country in countries
            for kw in kws[:8]
            for tpl in QUERY_TEMPLATES
        ]

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        all_queries = self.queries(ctx)
        per_run = int(ctx.config.get("queries_per_run", 3))
        cursor = int(ctx.state.get("cursor", 0)) % max(len(all_queries), 1)
        batch = [all_queries[(cursor + i) % len(all_queries)] for i in range(per_run)]
        ctx.state["cursor"] = cursor + per_run

        leads: list[RawLead] = []
        seen: set[str] = set()
        now = datetime.now(UTC)
        for query, country in batch:
            results = await ctx.adapters.search.search(
                ctx.session,
                query,
                agent="discovery",
                country=country,
                language="en",
                freshness=ctx.config.get("freshness"),
            )
            for r in results:
                if r.url in seen:
                    continue
                seen.add(r.url)
                posted_at = None
                if is_social_url(r.url):
                    posted_at = parse_result_age(r.age, now)
                    if not is_fresh_post(posted_at, ctx.settings, now):
                        continue
                text = f"{r.title}\n{r.description}"
                page_text = None
                if not ctx.adapters.search.is_mock:
                    try:
                        page = await ctx.adapters.fetcher.get(r.url)
                        if page.ok and "html" in page.headers.get("content-type", "html"):
                            page_text = main_text(page.text, r.url)
                            text = f"{page_title(page.text) or r.title}\n{page_text}"
                    except FetchError as exc:
                        logger.info("skip %s: %s", r.url, exc)
                        continue
                if not looks_like_request(text):
                    continue
                leads.append(
                    RawLead(
                        source_item_id=hashlib.sha256(r.url.encode()).hexdigest()[:40],
                        source_url=r.url,
                        trigger_type=TriggerType.EXPLICIT_REQUEST,
                        title=r.title[:500],
                        text=(page_text or r.description)[:8000],
                        posted_at=posted_at,
                        evidence_kind=EvidenceKind.PAGE,
                        evidence_data={"query": query, "search_country": country, "age": r.age},
                        business=None,
                    )
                )
        return leads
