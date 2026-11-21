"""Public social posts via Apify: Facebook (public pages and groups) and Instagram.

Apify serves only these two platforms (one token); X and Reddit come from the licensed social
providers. Public content only, read-only. Queries rotate per run to stay inside the free cap,
and a failed or empty run marks the source degraded.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.adapters.apify import ApifyClient, ApifyRunError
from app.adapters.social import parse_time
from app.agents.discovery.base import (
    RawLead,
    SourceContext,
    SourceDegradedError,
    industry_keywords,
)
from app.agents.discovery.freshness import is_fresh_post, max_post_age
from app.db.models import EvidenceKind, TriggerType
from app.orchestrator.quota import FreeQuotaExceededError

logger = logging.getLogger(__name__)

INTENT_PHRASES = [
    "need a website",
    "looking for a web designer",
    "need a logo",
    "website redesign",
    "recommend a web designer",
    "need help with SEO",
    "who can build a website",
]
IG_HASHTAGS = [
    "needawebsite",
    "needalogo",
    "websiteneeded",
    "lookingforwebdesigner",
    "smallbusinessowner",
    "newbusiness",
]


def _rotate(ctx: SourceContext, key: str, items: list[Any], n: int = 1) -> list[Any]:
    """Pick the next `n` items (round robin, persisted in source state)."""
    if not items:
        return []
    i = int(ctx.state.get(key, 0)) % len(items)
    ctx.state[key] = (i + n) % len(items)
    return [items[(i + k) % len(items)] for k in range(min(n, len(items)))]


async def _run(
    ctx: SourceContext, actor: str, actor_input: dict[str, Any], *, source: str
) -> list[dict[str, Any]]:
    token = ctx.settings.apify_api_token
    assert token is not None  # the runner only calls sources whose integration is ready
    try:
        run = await ApifyClient(token.get_secret_value()).run_actor(
            ctx.session,
            actor,
            actor_input,
            source=source,
            max_items=int(ctx.config.get("max_items", 10)),
            max_charge_usd=float(ctx.config.get("max_charge_usd", 0.1)),
        )
    except FreeQuotaExceededError as exc:
        if not exc.scope.startswith("apify:account"):
            raise  # our own monthly cap: paused, not degraded
        raise SourceDegradedError(str(exc)) from exc
    except ApifyRunError as exc:
        raise SourceDegradedError(str(exc)) from exc
    return run.items


def _lead(
    platform: str,
    item_id: str,
    url: str,
    text: str,
    *,
    handle: str | None = None,
    posted: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> RawLead:
    return RawLead(
        source_item_id=f"{platform}:{item_id}"[:300],
        source_url=url,
        trigger_type=TriggerType.EXPLICIT_REQUEST,
        title=text[:140],
        text=text[:8000],
        author=handle,
        posted_at=posted,
        evidence_kind=EvidenceKind.POST,
        evidence_data={
            "platform": platform,
            "handle": handle,
            "posted_at": posted.isoformat() if posted else None,
            "provider": "apify",
            **(extra or {}),
        },
    )


class _ApifySource:
    integration: ClassVar[str] = "apify"
    default_enabled: ClassVar[bool] = True


class ApifyInstagramSource(_ApifySource):
    key: ClassVar[str] = "social_instagram"
    label: ClassVar[str] = "Instagram (via Apify)"
    description: ClassVar[str] = "Public posts on request hashtags (need a website / logo)."
    default_schedule_minutes: ClassVar[int] = 480  # matches the 8-hour post cutoff
    constraints: ClassVar[str] = (
        "Licensed provider (apify/instagram-hashtag-scraper); public hashtag posts only; "
        "read-only; capped per run and per month."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "actor": "apify/instagram-hashtag-scraper",
        "hashtags": IG_HASHTAGS,
        "max_items": 10,
        "max_charge_usd": 0.03,
    }

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        tags = _rotate(ctx, "tag", list(ctx.config.get("hashtags") or IG_HASHTAGS), 1)
        items = await _run(
            ctx,
            str(ctx.config.get("actor", self.default_config["actor"])),
            {
                "hashtags": tags,
                "resultsType": "posts",
                "resultsLimit": int(ctx.config.get("max_items", 10)),
            },
            source=self.key,
        )
        leads = []
        for p in items:
            if not p.get("id") or not p.get("url"):
                continue
            posted = parse_time(p.get("timestamp"))
            if not is_fresh_post(posted, ctx.settings):
                continue
            leads.append(
                _lead(
                    "instagram",
                    str(p["id"]),
                    str(p["url"]),
                    str(p.get("caption") or ""),
                    handle=p.get("ownerUsername"),
                    posted=posted,
                    extra={
                        "hashtag": tags[0] if tags else None,
                        "owner_name": p.get("ownerFullName"),
                    },
                )
            )
        if not items:
            ctx.state["_degraded"] = "actor returned no posts"
        return leads


class ApifyFacebookSource(_ApifySource):
    key: ClassVar[str] = "social_facebook"
    label: ClassVar[str] = "Facebook (via Apify)"
    description: ClassVar[str] = (
        "Public posts asking for a web designer (keyword search) plus configured PUBLIC "
        "groups and pages."
    )
    default_schedule_minutes: ClassVar[int] = 480  # matches the 8-hour post cutoff
    constraints: ClassVar[str] = (
        "Licensed provider (apify/facebook-search-scraper, facebook-groups-scraper, "
        "facebook-posts-scraper); public pages and PUBLIC groups only — never private groups "
        "or profiles behind a login; read-only."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "search_actor": "apify/facebook-search-scraper",
        "groups_actor": "apify/facebook-groups-scraper",
        "pages_actor": "apify/facebook-posts-scraper",
        "public_group_urls": [],
        "public_page_urls": [],
        "max_items": 8,
        "max_charge_usd": 0.06,
    }

    def keywords(self, ctx: SourceContext) -> list[str]:
        trades = industry_keywords(ctx.runtime.active_industry)[:6]
        base = [
            "looking for web designer",
            "need a website for my business",
            "need a logo for my business",
            "recommend a website designer",
        ]
        return base + [f"website for my {t} business" for t in trades[:3]]

    @staticmethod
    def _map(p: dict[str, Any], origin: str) -> RawLead | None:
        url = p.get("url") or p.get("postUrl") or p.get("facebookUrl")
        text = str(p.get("text") or p.get("postText") or p.get("message") or "")
        pid = (
            p.get("postId")
            or p.get("id")
            or (hashlib.sha256(str(url).encode()).hexdigest()[:32] if url else None)
        )
        if not url or not pid or not text.strip():
            return None
        user = p.get("user") if isinstance(p.get("user"), dict) else {}
        handle = (user or {}).get("name") or (
            p.get("pageName") if isinstance(p.get("pageName"), str) else None
        )
        return _lead(
            "facebook",
            str(pid),
            str(url),
            text,
            handle=handle,
            posted=parse_time(p.get("time") or p.get("timestamp")),
            extra={"origin": origin, "group": p.get("groupTitle")},
        )

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        leads: list[RawLead] = []
        n = int(ctx.config.get("max_items", 8))
        kw = _rotate(ctx, "kw", self.keywords(ctx), 1)
        items = await _run(
            ctx,
            str(ctx.config.get("search_actor")),
            {"categories": kw, "searchType": "posts", "resultsLimit": n},
            source=self.key,
        )
        leads += [
            x
            for p in items
            if (x := self._map(p, "search")) and is_fresh_post(x.posted_at, ctx.settings)
        ]
        groups = [g for g in ctx.config.get("public_group_urls") or [] if "/groups/" in str(g)]
        if groups:
            since = (datetime.now(UTC) - max_post_age(ctx.settings)).strftime(
                "%Y-%m-%d"
            )  # day precision; filtered below
            g = _rotate(ctx, "group", groups, 1)
            try:
                gitems = await _run(
                    ctx,
                    str(ctx.config.get("groups_actor")),
                    {
                        "startUrls": [{"url": u} for u in g],
                        "resultsLimit": n,
                        "viewOption": "CHRONOLOGICAL",
                        "onlyPostsNewerThan": since,
                    },
                    source=self.key,
                )
                leads += [
                    x
                    for p in gitems
                    if (x := self._map(p, "public_group"))
                    and is_fresh_post(x.posted_at, ctx.settings)
                ]
            except SourceDegradedError as exc:
                ctx.state["_degraded"] = f"group run failed: {exc}"
        pages = list(ctx.config.get("public_page_urls") or [])
        if pages:
            pg = _rotate(ctx, "page", pages, 1)
            try:
                pitems = await _run(
                    ctx,
                    str(ctx.config.get("pages_actor")),
                    {"startUrls": [{"url": u} for u in pg], "resultsLimit": n},
                    source=self.key,
                )
                leads += [
                    x
                    for p in pitems
                    if (x := self._map(p, "public_page"))
                    and is_fresh_post(x.posted_at, ctx.settings)
                ]
            except SourceDegradedError as exc:
                ctx.state["_degraded"] = f"page run failed: {exc}"
        if not items and not leads:
            ctx.state["_degraded"] = "actor returned no posts"
        return leads
