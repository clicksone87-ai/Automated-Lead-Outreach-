"""Public social posts via Apify: X, Instagram, Facebook (public pages and groups) and Reddit.

Public content only, read-only. Queries rotate per run to stay inside the free cap, a failed or
empty run marks the source degraded, and posts deleted on Reddit are removed by
`sync_reddit_deletions`.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.apify import ApifyClient, ApifyRunError
from app.agents.discovery.base import (
    RawLead,
    SourceContext,
    SourceDegradedError,
    industry_keywords,
)
from app.db.models import EvidenceKind, Lead, TriggerType

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
REDDIT_SUBS = ["smallbusiness", "Entrepreneur", "forhire", "web_design", "startups"]
REDDIT_CITY_SUBS = [
    "nyc",
    "LosAngeles",
    "chicago",
    "houston",
    "Austin",
    "Dallas",
    "Atlanta",
    "Seattle",
    "london",
    "manchester",
    "birmingham",
    "Leeds",
    "glasgow",
    "bristol",
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


def _client(ctx: SourceContext) -> ApifyClient:
    token = ctx.settings.apify_api_token
    assert token is not None  # the runner only calls sources whose integration is ready
    return ApifyClient(token.get_secret_value())


MAX_POST_AGE: timedelta = timedelta(hours=8)


def _is_recent(dt: datetime | None, max_age: timedelta = MAX_POST_AGE) -> bool:
    """Strict post age cutoff: only posts from the last 8 hours (V2 Social Sources).

    Undated items are dropped because they cannot prove that a post is fresh.
    Every dated item older than the cutoff is dropped.
    """
    if dt is None:
        return False
    return dt >= datetime.now(UTC) - max_age


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(s)  # X: "Mon Nov 16 15:39:23 +0000 2026"
        except (TypeError, ValueError):
            try:
                return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                return None


async def _run(
    ctx: SourceContext, actor: str, actor_input: dict[str, Any], *, source: str
) -> list[dict[str, Any]]:
    max_items = int(ctx.config.get("max_items", 10))
    max_charge = float(ctx.config.get("max_charge_usd", 0.1))
    try:
        run = await _client(ctx).run_actor(
            ctx.session,
            actor,
            actor_input,
            source=source,
            max_items=max_items,
            max_charge_usd=max_charge,
        )
    except ApifyRunError as exc:
        raise SourceDegradedError(str(exc)) from exc
    return run.items


def _lead(
    platform: str,
    item_id: str,
    url: str,
    text: str,
    *,
    title: str | None = None,
    handle: str | None = None,
    posted: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> RawLead:
    return RawLead(
        source_item_id=f"{platform}:{item_id}"[:300],
        source_url=url,
        trigger_type=TriggerType.EXPLICIT_REQUEST,
        title=(title or text)[:140],
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


class ApifyXSource(_ApifySource):
    key: ClassVar[str] = "social_x"
    label: ClassVar[str] = "X (via Apify)"
    description: ClassVar[str] = "Recent public posts asking for a website, logo or SEO help."
    default_schedule_minutes: ClassVar[int] = 360
    constraints: ClassVar[str] = (
        "Licensed provider (Apify actor apidojo/tweet-scraper); public posts only; read-only; "
        "capped per run and per month."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "actor": "apidojo/tweet-scraper",
        "max_items": 20,
        "max_charge_usd": 0.02,
    }

    def queries(self, ctx: SourceContext) -> list[str]:
        trades = industry_keywords(ctx.runtime.active_industry)[:8]
        trade_q = " OR ".join(trades[:4])
        return [f'"{p}" ({trade_q}) -is:retweet' for p in INTENT_PHRASES]

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        terms = _rotate(ctx, "q", self.queries(ctx), 2)
        items = await _run(
            ctx,
            str(ctx.config.get("actor", self.default_config["actor"])),
            {
                "searchTerms": terms,
                "sort": "Latest",
                "tweetLanguage": "en",
                "maxItems": int(ctx.config.get("max_items", 20)),
            },
            source=self.key,
        )
        leads = []
        for t in items:
            if t.get("isRetweet") or not t.get("id"):
                continue
            posted = _parse_dt(t.get("createdAt"))
            if not _is_recent(posted):
                continue
            text = str(t.get("fullText") or t.get("text") or "")
            author = t.get("author") or {}
            handle = author.get("userName") if isinstance(author, dict) else None
            url = str(t.get("url") or f"https://x.com/i/web/status/{t['id']}")
            leads.append(
                _lead(
                    "x",
                    str(t["id"]),
                    url,
                    text,
                    handle=handle,
                    posted=posted,
                    extra={
                        "lang": t.get("lang"),
                        "location": author.get("location") if isinstance(author, dict) else None,
                    },
                )
            )
        if not items:
            ctx.state["_degraded"] = "actor returned no posts"
        return leads


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
            posted = _parse_dt(p.get("timestamp"))
            if not _is_recent(posted):
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
            posted=_parse_dt(p.get("time") or p.get("timestamp")),
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
        leads += [x for p in items if (x := self._map(p, "search")) and _is_recent(x.posted_at)]
        groups = [g for g in ctx.config.get("public_group_urls") or [] if "/groups/" in str(g)]
        if groups:
            since = (datetime.now(UTC) - MAX_POST_AGE).strftime(
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
                    if (x := self._map(p, "public_group")) and _is_recent(x.posted_at)
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
                    if (x := self._map(p, "public_page")) and _is_recent(x.posted_at)
                ]
            except SourceDegradedError as exc:
                ctx.state["_degraded"] = f"page run failed: {exc}"
        if not items and not leads:
            ctx.state["_degraded"] = "actor returned no posts"
        return leads


class ApifyRedditSource(_ApifySource):
    key: ClassVar[str] = "social_reddit"
    label: ClassVar[str] = "Reddit (via Apify)"
    description: ClassVar[str] = (
        "New posts in small-business, freelance and US/UK city subreddits that ask for our "
        "services."
    )
    default_schedule_minutes: ClassVar[int] = 360
    constraints: ClassVar[str] = (
        "Licensed provider (fatihtahta/reddit-scraper-search-fast); public posts only; "
        "read-only, no automated messages; posts deleted on Reddit are deleted from our DB."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "actor": "fatihtahta/reddit-scraper-search-fast",
        "subreddits": REDDIT_SUBS + REDDIT_CITY_SUBS,
        "keywords": ["website", "web designer", "logo", "SEO"],
        "max_items": 8,
        "max_charge_usd": 0.02,
    }

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        sub = _rotate(ctx, "sub", list(ctx.config.get("subreddits") or REDDIT_SUBS), 1)[0]
        items = await _run(
            ctx,
            str(ctx.config.get("actor", self.default_config["actor"])),
            {
                "subredditName": sub,
                "subredditKeywords": list(ctx.config.get("keywords") or ["website"]),
                "subredditSort": "new",
                "subredditTimeframe": "day",
                "maxPosts": int(ctx.config.get("max_items", 8)),
                "scrapeComments": False,
            },
            source=self.key,
        )
        leads = []
        for p in items:
            if p.get("kind", "post") != "post" or p.get("is_deleted_or_removed"):
                continue
            posted = _parse_dt(p.get("created_utc"))
            if not _is_recent(posted):
                continue
            text = f"{p.get('title') or ''}\n\n{p.get('body') or ''}".strip()
            url = str(p.get("url") or f"https://www.reddit.com{p.get('permalink', '')}")
            leads.append(
                _lead(
                    "reddit",
                    str(p.get("id")),
                    url,
                    text,
                    title=p.get("title"),
                    handle=p.get("author"),
                    posted=posted,
                    extra={"subreddit": p.get("subreddit")},
                )
            )
        if not items:
            ctx.state["_degraded"] = f"no posts returned for r/{sub}"
        return leads


async def sync_reddit_deletions(
    session: AsyncSession, client: ApifyClient, *, limit: int = 20, max_age_days: int = 30
) -> int:
    """Re-check stored Reddit posts; delete those deleted/removed on Reddit. Returns count."""
    from app.agents.discovery.sources.reddit import delete_lead_content  # noqa: PLC0415

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    rows = (
        (
            await session.execute(
                select(Lead)
                .where(Lead.source == ApifyRedditSource.key, Lead.created_at >= cutoff)
                .order_by(Lead.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    by_url = {lead.source_url: lead for lead in rows if lead.source_url}
    run = await client.run_actor(
        session,
        ApifyRedditSource.default_config["actor"],
        {"urls": list(by_url), "scrapeComments": False, "maxPosts": len(by_url)},
        source=ApifyRedditSource.key,
        max_items=len(by_url),
        max_charge_usd=round(0.002 * len(by_url) + 0.01, 4),
    )
    seen: dict[str, dict[str, Any]] = {}
    for p in run.items:
        for k in ("url", "canonical_url"):
            if p.get(k):
                seen[str(p[k]).rstrip("/")] = p
    deleted = 0
    for url, lead in by_url.items():
        post = seen.get(url.rstrip("/"))
        if post is None:
            # Missing from a non-empty result = no longer available. An empty result says
            # nothing (actor problem), so nothing is deleted then.
            gone = bool(run.items)
        else:
            gone = (
                bool(post.get("is_deleted_or_removed"))
                or str(post.get("author")) == "[deleted]"
                or str(post.get("body") or "").strip() in ("[deleted]", "[removed]")
            )
        if gone:
            await delete_lead_content(session, lead)
            deleted += 1
    await session.flush()
    return deleted
