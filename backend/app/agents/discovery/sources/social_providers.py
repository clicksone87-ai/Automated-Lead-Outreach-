"""Public social posts via licensed data providers (EnsembleData, ScrapeCreators, SocialCrawl,
HikerAPI): TikTok, Reddit, X, Threads, YouTube, Instagram, Facebook and LinkedIn.

Each run searches a few platforms (rotating) with one intent phrase each. Only posts from the
last SOCIAL_MAX_POST_AGE_HOURS are kept; sellers are rejected later by the prefilter.
Read-only: nothing is ever posted.

Cap math (defaults: every 60 min, platforms_per_run=3, SOCIAL_PROVIDER_DAILY_CAP=50 credits per
provider per day): 24 runs x 3 = 72 searches a day over the 8 provider platforms, so each
platform is searched 9 times a day, once every 160 minutes, well inside the 8-hour window.
First-choice provider per day: EnsembleData 9 (TikTok); ScrapeCreators 45 (Reddit, Threads,
YouTube, Instagram, LinkedIn, 9 each); SocialCrawl 18 (X and Facebook, 9 each). Fallback
calls (SocialCrawl Reddit 1 credit, LinkedIn 5) only run when the first provider fails, and a
provider at its cap is skipped, so no provider can exceed 50 a day. Pinterest, Twitch, GitHub
and Linktree have no post search at any provider; the Scout source covers them as profiles.
"""

from __future__ import annotations

import logging
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
from app.agents.discovery.freshness import is_fresh_post
from app.agents.discovery.sources.social_apify import IG_HASHTAGS, INTENT_PHRASES
from app.config import Settings
from app.db.models import EvidenceKind, TriggerType

logger = logging.getLogger(__name__)


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
        "Recent public posts on TikTok, Reddit, X, Threads, YouTube, Instagram, Facebook and "
        "LinkedIn asking for a website, logo or SEO help."
    )
    integration: ClassVar[str] = "social_providers"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 60
    constraints: ClassVar[str] = (
        "Licensed data providers; public posts only; read-only; each provider capped per day "
        "(SOCIAL_PROVIDER_DAILY_CAP). LinkedIn only through the providers' LinkedIn endpoints: "
        "linkedin.com is never fetched, no cookies or logins. Posts older than "
        "SOCIAL_MAX_POST_AGE_HOURS or without a timestamp are dropped."
    )
    default_config: ClassVar[dict[str, Any]] = {"platforms_per_run": 3}

    def platforms(self, ctx: SourceContext, router: SocialRouter) -> list[str]:
        wanted = set(ctx.settings.social_platform_list)
        return [p for p in router.platforms() if p in wanted]

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        if not ctx.settings.social_sources_enabled:
            return []
        router = build_router(ctx.settings)
        try:
            return await self._fetch(ctx, router)
        finally:
            await router.aclose()

    async def _fetch(self, ctx: SourceContext, router: SocialRouter) -> list[RawLead]:
        platforms = self.platforms(ctx, router)
        if not platforms:
            return []
        per_run = min(int(ctx.config.get("platforms_per_run", 3)), len(platforms))
        start = int(ctx.state.get("platform_cursor", 0)) % len(platforms)
        ctx.state["platform_cursor"] = start + per_run
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
            fresh = [p for p in posts if is_fresh_post(p.posted_at, ctx.settings)]
            stats[platform] = {"seen": len(posts), "fresh": len(fresh), "query": query}
            leads += [to_raw_lead(p, query) for p in fresh]
        if failures and not leads:
            ctx.state["_degraded"] = "; ".join(failures)[:500]
        return leads
