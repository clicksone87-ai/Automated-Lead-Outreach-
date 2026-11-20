"""Public social posts via licensed data providers (EnsembleData, ScrapeCreators, SocialCrawl,
HikerAPI): TikTok, Reddit, X, Threads, YouTube, Instagram and Facebook.

Each run searches a few platforms (rotating) with one intent phrase each, so every provider
stays inside SOCIAL_PROVIDER_DAILY_CAP. Only posts from the last SOCIAL_MAX_POST_AGE_HOURS are
kept; sellers are rejected later by the prefilter. Read-only: nothing is ever posted.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, ClassVar

from app.adapters.social import (
    EnsembleData,
    HikerApi,
    ScrapeCreators,
    SocialCrawl,
    SocialPost,
    SocialProviderError,
    SocialRouter,
)
from app.agents.discovery.base import RawLead, SourceContext
from app.agents.discovery.sources.social_apify import IG_HASHTAGS, INTENT_PHRASES, _is_recent
from app.config import Settings
from app.db.models import EvidenceKind, TriggerType

logger = logging.getLogger(__name__)
# LinkedIn is never searched: the project forbids LinkedIn scraping, also through third parties.
NEVER = frozenset({"linkedin"})


def _secret(v: Any) -> str:
    return v.get_secret_value() if v is not None else ""


def build_router(s: Settings) -> SocialRouter:
    providers: list[Any] = []
    if _secret(s.ensembledata_api_token):
        providers.append(EnsembleData(_secret(s.ensembledata_api_token)))
    if _secret(s.scrapecreators_api_key):
        providers.append(ScrapeCreators(_secret(s.scrapecreators_api_key)))
    if _secret(s.socialcrawl_api_key):
        providers.append(SocialCrawl(_secret(s.socialcrawl_api_key)))
    if _secret(s.hikerapi_access_key):
        providers.append(HikerApi(_secret(s.hikerapi_access_key), s.hikerapi_base_url))
    return SocialRouter(providers)


def to_raw_lead(p: SocialPost, query: str) -> RawLead:
    return RawLead(
        source_item_id=f"{p.platform}:{p.id}"[:300],
        source_url=p.url,
        trigger_type=TriggerType.EXPLICIT_REQUEST,
        title=(p.text or p.url)[:140],
        text=p.text[:8000],
        author=p.author,
        posted_at=p.posted_at,
        evidence_kind=EvidenceKind.POST,
        evidence_data={
            "platform": p.platform,
            "handle": p.author,
            "posted_at": p.posted_at.isoformat() if p.posted_at else None,
            "provider": p.provider,
            "query": query,
            **p.extra,
        },
    )


class SocialProvidersSource:
    key: ClassVar[str] = "social_providers"
    label: ClassVar[str] = "Social posts (EnsembleData, ScrapeCreators, SocialCrawl, HikerAPI)"
    description: ClassVar[str] = (
        "Recent public posts on TikTok, Reddit, X, Threads, YouTube, Instagram and Facebook "
        "asking for a website, logo or SEO help."
    )
    integration: ClassVar[str] = "social_providers"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 60
    constraints: ClassVar[str] = (
        "Licensed data providers; public posts only; read-only; each provider capped per day "
        "(SOCIAL_PROVIDER_DAILY_CAP). LinkedIn is never searched."
    )
    default_config: ClassVar[dict[str, Any]] = {"platforms_per_run": 3}

    def platforms(self, ctx: SourceContext, router: SocialRouter) -> list[str]:
        wanted = set(ctx.settings.social_platform_list) - NEVER
        return [p for p in router.platforms() if p in wanted]

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        if not ctx.settings.social_sources_enabled:
            return []
        router = build_router(ctx.settings)
        platforms = self.platforms(ctx, router)
        if not platforms:
            return []
        per_run = min(int(ctx.config.get("platforms_per_run", 3)), len(platforms))
        start = int(ctx.state.get("platform_cursor", 0)) % len(platforms)
        ctx.state["platform_cursor"] = start + per_run
        max_age = timedelta(hours=ctx.settings.social_max_post_age_hours)
        stats = ctx.state.setdefault("stats", {})
        leads: list[RawLead] = []
        failures: list[str] = []
        for k in range(per_run):
            platform = platforms[(start + k) % len(platforms)]
            terms = IG_HASHTAGS if platform == "instagram" else INTENT_PHRASES
            qi = int(ctx.state.get(f"q_{platform}", 0)) % len(terms)
            ctx.state[f"q_{platform}"] = qi + 1
            query = terms[qi]
            try:
                posts = await router.search(ctx.session, platform, query, agent="discovery")
            except SocialProviderError as exc:
                failures.append(str(exc)[:200])
                continue
            fresh = [p for p in posts if _is_recent(p.posted_at, max_age)]
            stats[platform] = {"seen": len(posts), "fresh": len(fresh), "query": query}
            leads += [to_raw_lead(p, query) for p in fresh]
        if failures and not leads:
            ctx.state["_degraded"] = "; ".join(failures)[:500]
        return leads
