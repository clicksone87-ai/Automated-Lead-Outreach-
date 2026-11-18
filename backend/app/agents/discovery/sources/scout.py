"""Scout: creator and business profiles that have no website of their own.

Finds TikTok, YouTube, Instagram, Pinterest, Linktree, Twitch and GitHub profiles of local
trade businesses through the licensed search API ("site:tiktok.com plumber Leeds"), reads each
profile within the platform rules (see `app.agents.scout.client`), and creates a
`profile_without_site` lead when the profile links to no website. Bio emails, phones and links
travel with the lead; Contact Enrichment verifies the email or falls back to a DM draft.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from app.adapters.search import SearchResult
from app.agents.discovery.base import RawBusiness, RawLead, SourceContext, industry_keywords
from app.agents.discovery.proactive_triggers.local_sites import (
    CITIES,
    TRADE_KEYWORDS_BY_LANG,
)
from app.agents.scout import parsers
from app.agents.scout.client import ScoutClient
from app.agents.scout.parsers import Profile
from app.db.models import EvidenceKind, TriggerType
from app.services.locale import COUNTRY_DEFAULT_LANGUAGE

logger = logging.getLogger(__name__)

SITE_FILTER = {
    "tiktok": "site:tiktok.com/@",
    "youtube": "site:youtube.com/@",
    "instagram": "site:instagram.com",
    "pinterest": "site:pinterest.com",
    "linktree": "site:linktr.ee",
    "twitch": "site:twitch.tv",
    "github": "site:github.com",
}
# Twitch and GitHub rarely hold local trade businesses; add them in the source config.
DEFAULT_PLATFORMS = ["tiktok", "youtube", "instagram", "linktree", "pinterest"]


def _stem(trade: str) -> str:
    """'plumber' → 'plum', 'roofing contractor' → 'roof', 'rørlegger' → 'rørl'."""
    word = (trade.split() or [""])[0].casefold()
    return word[:4] if len(word) >= 4 else word


def relevant(p: Profile, trade: str) -> bool:
    """The account itself must look like the trade: in its handle, its name, or a real bio.

    Drops news, schools and fans that merely posted about the trade. A bio that came from a
    post's search snippet (`from_post`) describes the post, so it does not count.
    """
    stem = _stem(trade)
    if not stem:
        return True
    texts = [p.username.replace(".", " ").replace("_", " "), p.full_name]
    if not p.extra.get("from_post"):
        texts.append(p.bio)
    return any(stem in t.casefold() for t in texts if t)


def lead_text(p: Profile, query: str) -> str:
    parts = [
        f"{parsers.LABELS.get(p.platform, p.platform)} profile @{p.username}"
        f" ({p.follower_count:,} followers), found for '{query}'. No website linked.",
        f"Bio: {p.bio.strip()}" if p.bio.strip() else "",
        f"Email in bio: {p.email}" if p.email else "",
        f"Phone in bio: {p.phone}" if p.phone else "",
        ("Links: " + ", ".join(p.links[:8])) if p.links else "",
    ]
    return "\n".join(x for x in parts if x)


class ScoutProfilesSource:
    key: ClassVar[str] = "scout_profiles"
    label: ClassVar[str] = "Scout: creator & business profiles"
    description: ClassVar[str] = (
        "TikTok, YouTube, Instagram, Pinterest, Linktree (plus Twitch and GitHub on request) "
        "profiles of local businesses with no website; bio emails, phones and links extracted."
    )
    integration: ClassVar[str] = "serper"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 360
    constraints: ClassVar[str] = (
        "Profiles found via the licensed search API. Pages read only where robots.txt allows "
        "(TikTok, YouTube, open link-in-bio hosts); official APIs for GitHub and Twitch; Apify "
        "for Instagram; Pinterest and Linktree from the search snippet only. No proxies, no "
        "user-agent rotation, no LinkedIn cookie, no email guessing or SMTP probing."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "platforms": DEFAULT_PLATFORMS,
        "queries_per_run": 1,
        "max_profiles": 8,
        "min_followers": 0,
        "skip_with_website": True,
    }

    def queries(self, ctx: SourceContext) -> list[tuple[str, str, str, str, str]]:
        """(platform, query, country, language, trade), rotating platform fastest."""
        platforms = [
            p for p in ctx.config.get("platforms") or DEFAULT_PLATFORMS if p in SITE_FILTER
        ]
        industry = ctx.runtime.active_industry
        countries = [c for c in (ctx.runtime.target_countries or ["GB", "US"]) if c in CITIES]
        out: list[tuple[str, str, str, str, str]] = []
        for c in countries:
            lang = COUNTRY_DEFAULT_LANGUAGE.get(c, "en")
            if industry == "home_services":
                trades = TRADE_KEYWORDS_BY_LANG.get(lang, TRADE_KEYWORDS_BY_LANG["en"])
            else:
                trades = industry_keywords(industry)[:8]
            for city in CITIES[c]:
                for t in trades:
                    for p in platforms:
                        out.append((p, f"{SITE_FILTER[p]} {t} {city}", c, lang, t))
        return out

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        queries = self.queries(ctx)
        if not queries:
            return []
        per_run = int(ctx.config.get("queries_per_run", 1))
        cursor = int(ctx.state.get("cursor", 0)) % len(queries)
        ctx.state["cursor"] = cursor + per_run
        client = ScoutClient(ctx.session, ctx.adapters, ctx.settings)
        max_profiles = int(ctx.config.get("max_profiles", 8))
        min_followers = int(ctx.config.get("min_followers", 0))
        stats = ctx.state.setdefault("stats", {})
        leads: list[RawLead] = []
        seen: set[tuple[str, str]] = set()
        for i in range(per_run):
            platform, query, country, lang, trade = queries[(cursor + i) % len(queries)]
            results = await ctx.adapters.search.search(
                ctx.session, query, agent="discovery", country=country, language=lang, count=10
            )
            for r in results:
                if len(seen) >= max_profiles:
                    break
                username = parsers.username_from_url(platform, r.url)
                if not username or (platform, username.lower()) in seen:
                    continue
                seen.add((platform, username.lower()))
                profile = await client.profile(platform, username, hint=r)
                if profile is None:
                    continue
                await self._expand_link_hub(client, profile)
                stats[platform] = int(stats.get(platform, 0)) + 1
                if not relevant(profile, trade):
                    continue
                if profile.follower_count < min_followers:
                    continue
                if profile.website and ctx.config.get("skip_with_website", True):
                    continue  # has its own site: the outdated-website trigger covers it
                leads.append(
                    self._lead(profile, query=query, country=country, lang=lang, ctx=ctx, hit=r)
                )
        return leads

    @staticmethod
    async def _expand_link_hub(client: ScoutClient, p: Profile) -> None:
        """A bio pointing at bio.link / lnk.bio / ... often holds the email and website."""
        hub = next((lk for lk in p.links if parsers.is_link_hub(lk)), None)
        if not hub or (p.email and p.website):
            return
        page = await client.link_hub(hub)
        if page is None:
            return
        p.email = p.email or page.email
        p.phone = p.phone or page.phone
        p.website = p.website or page.website
        p.links = list(dict.fromkeys([*p.links, *page.links]))[:20]

    @staticmethod
    def _lead(
        p: Profile, *, query: str, country: str, lang: str, ctx: SourceContext, hit: SearchResult
    ) -> RawLead:
        city = next((c for c in CITIES.get(country, []) if query.endswith(c)), "")
        name = p.full_name or p.username
        return RawLead(
            source_item_id=f"scout:{p.platform}:{p.username.lower()}",
            source_url=p.profile_url,
            trigger_type=TriggerType.PROFILE_WITHOUT_SITE,
            title=f"{name} — {parsers.LABELS.get(p.platform, p.platform)} profile, no website",
            text=lead_text(p, query),
            author=p.username,
            business=RawBusiness(
                name=name[:200],
                email=p.email or None,
                phone=p.phone or None,
                address=city or None,
                country_hint=country,
                industry=ctx.runtime.active_industry,
                has_website=False,
            ),
            evidence_kind=EvidenceKind.PAGE,
            evidence_data={
                "platform": p.platform,
                "handle": p.username,
                "followers": p.follower_count,
                "links": p.links[:20],
                "via": p.via,
                "search_title": hit.title,
                "provider": "scout",
                **{k: v for k, v in p.extra.items() if isinstance(v, str | int | bool)},
            },
            language_hint=lang,
        )
