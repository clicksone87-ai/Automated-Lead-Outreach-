"""Licensed social-data providers for PUBLIC posts: EnsembleData, ScrapeCreators, SocialCrawl
and HikerAPI. Read-only keyword/hashtag search; nothing is ever posted.

Every call is counted against the provider's daily cap (SOCIAL_PROVIDER_DAILY_CAP, costs table)
before it is made. `SocialRouter` tries the providers that serve a platform in order and moves to
the next one when a provider is out of quota or fails. Field paths were checked against live
responses on 2026-11-21, except where a parser says UNVERIFIED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator import quota
from app.orchestrator.budget import BudgetExceededError

logger = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(45, connect=10)


@dataclass(slots=True)
class SocialPost:
    platform: str
    id: str
    url: str
    text: str
    author: str | None
    posted_at: datetime | None
    provider: str
    extra: dict[str, Any] = field(default_factory=dict)


class SocialProviderError(Exception):
    pass


def parse_time(value: Any) -> datetime | None:
    """Unix seconds (int or digit string), ISO 8601, or X's RFC 2822-like dates."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(float(value), UTC)
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _aweme(item: dict[str, Any], provider: str) -> SocialPost | None:
    """TikTok `aweme_info` (EnsembleData keyword search; ScrapeCreators uses the same object)."""
    inner = item.get("aweme_info")
    a: dict[str, Any] = inner if isinstance(inner, dict) else item
    vid, author = a.get("aweme_id"), _get(a, "author", "unique_id")
    if not vid:
        return None
    url = (
        str(a.get("share_url") or "").split("?")[0]
        or f"https://www.tiktok.com/@{author}/video/{vid}"
    )
    return SocialPost("tiktok", str(vid), url, str(a.get("desc") or ""), author,
                      parse_time(a.get("create_time")), provider, {"region": a.get("region")})  # fmt: skip


class _Provider:
    name: ClassVar[str]
    platforms: ClassVar[frozenset[str]]

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def _get_json(
        self, session: AsyncSession, url: str, *, params: dict[str, Any], headers: dict[str, str],
        agent: str, what: str,
    ) -> dict[str, Any]:  # fmt: skip
        await quota.consume(session, self.name, agent=agent, description=what[:200])
        resp = await self._client.get(url, params=params, headers=headers)
        if resp.status_code == 404:
            return {}  # "no posts found" (not charged by the providers)
        if resp.status_code >= 400:
            raise SocialProviderError(f"{self.name} HTTP {resp.status_code}: {resp.text[:160]}")
        body = resp.json()
        if isinstance(body, dict) and body.get("success") is False:
            raise SocialProviderError(f"{self.name}: {str(body.get('message'))[:160]}")
        return body if isinstance(body, dict) else {"items": body}

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        raise NotImplementedError


class EnsembleData(_Provider):
    """ensembledata.com: `token` query parameter; free trial 50 units/day, 1 unit per search."""

    name: ClassVar[str] = "ensembledata"
    platforms: ClassVar[frozenset[str]] = frozenset({"tiktok"})
    BASE = "https://ensembledata.com/apis"

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        body = await self._get_json(
            session, f"{self.BASE}/tt/keyword/search",
            params={"name": query, "period": "1", "sorting": "2", "token": self._key},
            headers={}, agent=agent, what=f"tiktok: {query}",
        )  # fmt: skip
        items = _get(body, "data", "data") or []
        return [p for i in items if isinstance(i, dict) and (p := _aweme(i, self.name))]


class ScrapeCreators(_Provider):
    """scrapecreators.com: `x-api-key` header, 1 credit per search."""

    name: ClassVar[str] = "scrapecreators"
    platforms: ClassVar[frozenset[str]] = frozenset(
        {"tiktok", "reddit", "x", "threads", "youtube", "instagram"}
    )
    BASE = "https://api.scrapecreators.com/v1"
    PATHS: ClassVar[dict[str, tuple[str, str, dict[str, str]]]] = {
        "tiktok": ("/tiktok/search/keyword", "query", {"trim": "true"}),
        "reddit": ("/reddit/search", "query", {"sort": "new", "timeframe": "day"}),
        "x": ("/twitter/search/posts", "query", {}),
        "threads": ("/threads/search", "query", {}),
        "youtube": ("/youtube/search", "query", {"uploadDate": "today"}),
        "instagram": ("/instagram/search/hashtag", "hashtag", {"date_posted": "last-day"}),
    }

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        path, param, extra = self.PATHS[platform]
        q = query.replace(" ", "").lstrip("#") if platform == "instagram" else query
        body = await self._get_json(
            session, f"{self.BASE}{path}", params={param: q, **extra},
            headers={"x-api-key": self._key}, agent=agent, what=f"{platform}: {query}",
        )  # fmt: skip
        parse = getattr(self, f"_{platform}")
        return [p for i in self._items(body, platform) if isinstance(i, dict) and (p := parse(i))]

    @staticmethod
    def _items(body: dict[str, Any], platform: str) -> list[Any]:
        key = {"tiktok": "search_item_list", "youtube": "videos"}.get(platform, "posts")
        return list(body.get(key) or [])

    def _tiktok(self, i: dict[str, Any]) -> SocialPost | None:
        return _aweme(i, self.name)  # UNVERIFIED for this provider: same aweme object assumed

    def _reddit(self, i: dict[str, Any]) -> SocialPost | None:
        if not i.get("id"):
            return None
        text = "\n\n".join(x for x in (i.get("title"), i.get("selftext")) if x)
        url = str(i.get("url") or f"https://www.reddit.com{i.get('permalink', '')}")
        return SocialPost("reddit", str(i["id"]), url, text, i.get("author"),
                          parse_time(i.get("created_utc")), self.name,
                          {"subreddit": i.get("subreddit")})  # fmt: skip

    def _x(self, i: dict[str, Any]) -> SocialPost | None:
        tid, legacy = i.get("rest_id"), i.get("legacy") or {}
        if not tid or legacy.get("retweeted_status_result"):
            return None
        handle = _get(i, "core", "user_results", "result", "core", "screen_name")
        url = str(i.get("url") or f"https://x.com/{handle or 'i/web'}/status/{tid}")
        return SocialPost("x", str(tid), url, str(legacy.get("full_text") or ""), handle,
                          parse_time(legacy.get("created_at")), self.name,
                          {"lang": legacy.get("lang")})  # fmt: skip

    def _threads(self, i: dict[str, Any]) -> SocialPost | None:
        code, user = i.get("code"), _get(i, "user", "username")
        if not code:
            return None
        return SocialPost("threads", str(i.get("pk") or code),
                          f"https://www.threads.net/@{user}/post/{code}",
                          str(_get(i, "caption", "text") or ""), user,
                          parse_time(i.get("taken_at")), self.name)  # fmt: skip

    def _youtube(self, i: dict[str, Any]) -> SocialPost | None:
        if not i.get("id"):
            return None
        channel = i.get("channel") or {}
        return SocialPost("youtube", str(i["id"]),
                          str(i.get("url") or f"https://www.youtube.com/watch?v={i['id']}"),
                          str(i.get("title") or ""), channel.get("handle") or channel.get("title"),
                          parse_time(i.get("publishedTime")), self.name)  # fmt: skip

    def _instagram(self, i: dict[str, Any]) -> SocialPost | None:
        # Fields from the provider's documented sample; no live post seen yet (404 "No posts").
        code = i.get("shortcode")
        if not code and not i.get("id"):
            return None
        caption = i.get("caption")
        text = caption.get("text") if isinstance(caption, dict) else caption
        return SocialPost("instagram", str(i.get("id") or code),
                          str(i.get("url") or f"https://www.instagram.com/p/{code}/"),
                          str(text or ""), _get(i, "owner", "username"),
                          parse_time(i.get("taken_at")), self.name)  # fmt: skip


class SocialCrawl(_Provider):
    """socialcrawl.dev: `x-api-key` header, unified post schema, 1 credit per search page."""

    name: ClassVar[str] = "socialcrawl"
    platforms: ClassVar[frozenset[str]] = frozenset({"tiktok", "facebook"})
    BASE = "https://www.socialcrawl.dev/v1"
    PATHS: ClassVar[dict[str, tuple[str, dict[str, str]]]] = {
        "tiktok": ("/tiktok/search", {"sort_by": "date-posted"}),
        "facebook": ("/facebook/search/posts", {}),
    }

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        path, extra = self.PATHS[platform]
        body = await self._get_json(
            session, f"{self.BASE}{path}", params={"query": query, **extra},
            headers={"x-api-key": self._key}, agent=agent, what=f"{platform}: {query}",
        )  # fmt: skip
        out = []
        for row in _get(body, "data", "items") or []:
            post = row.get("post") if isinstance(row, dict) else None
            if not isinstance(post, dict) or not post.get("id") or not post.get("url"):
                continue
            author = _get(post, "author", "username") or _get(post, "author", "display_name")
            out.append(SocialPost(platform, str(post["id"]), str(post["url"]),
                                  str(_get(post, "content", "text") or ""), author,
                                  parse_time(post.get("published_at")), self.name,
                                  {"language": _get(row, "computed", "language")}))  # fmt: skip
        return out


class HikerApi(_Provider):
    """HikerAPI (Instagram): `x-access-key` header; /g2/hashtag/medias (relevance-ranked).
    UNVERIFIED: no key yet, so the response shape is from the provider's docs only."""

    name: ClassVar[str] = "hikerapi"
    platforms: ClassVar[frozenset[str]] = frozenset({"instagram"})

    def __init__(
        self, api_key: str, base_url: str = "https://api.hikerapi.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:  # fmt: skip
        super().__init__(api_key, client)
        self._base = base_url.rstrip("/")

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        tag = query.replace(" ", "").lstrip("#")
        body = await self._get_json(
            session, f"{self._base}/g2/hashtag/medias", params={"name": tag},
            headers={"x-access-key": self._key}, agent=agent, what=f"instagram: #{tag}",
        )  # fmt: skip
        items = body.get("items") or body.get("medias") or _get(body, "response", "items") or []
        out = []
        for m in items:
            if not isinstance(m, dict) or not m.get("code"):
                continue
            caption = m.get("caption")
            text = caption.get("text") if isinstance(caption, dict) else m.get("caption_text")
            out.append(SocialPost("instagram", str(m.get("pk") or m["code"]),
                                  f"https://www.instagram.com/p/{m['code']}/", str(text or ""),
                                  _get(m, "user", "username"), parse_time(m.get("taken_at")),
                                  self.name))  # fmt: skip
        return out


# Which providers serve each platform, best first (free daily units before one-time credits).
PLATFORM_ORDER: dict[str, tuple[str, ...]] = {
    "tiktok": ("ensembledata", "socialcrawl", "scrapecreators"),
    "reddit": ("scrapecreators",),
    "x": ("scrapecreators",),
    "threads": ("scrapecreators",),
    "youtube": ("scrapecreators",),
    "instagram": ("hikerapi", "scrapecreators"),
    "facebook": ("socialcrawl",),
}


class SocialRouter:
    """Per-platform provider chain: the next provider takes over on a cap or an error."""

    def __init__(self, providers: list[_Provider]) -> None:
        self.providers = {p.name: p for p in providers}

    def platforms(self) -> list[str]:
        return [
            pl for pl, order in PLATFORM_ORDER.items() if any(n in self.providers for n in order)
        ]

    async def search(
        self, session: AsyncSession, platform: str, query: str, *, agent: str
    ) -> list[SocialPost]:
        last: Exception | None = None
        for name in PLATFORM_ORDER.get(platform, ()):
            provider = self.providers.get(name)
            if provider is None:
                continue
            try:
                async with session.begin_nested():
                    return await provider.search(session, platform, query, agent=agent)
            except (SocialProviderError, BudgetExceededError, httpx.HTTPError, ValueError) as exc:
                logger.warning("social %s via %s failed (%s); trying the next", platform, name, exc)
                await quota.record_provider_error(name, f"{type(exc).__name__}: {exc}")
                last = exc
        if last is not None:
            raise SocialProviderError(f"every provider failed for {platform}: {last}")
        return []
