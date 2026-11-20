"""More free-tier search providers for the search router: SerpAPI, ScrapingDog, Searlo, Tavily.

Every call is counted against the provider's own cap (orchestrator.quota) before it is made.
The router (search_free.FallbackSearch) tries them in SEARCH_PROVIDER_ORDER and moves on when
one is out of quota or answers 401/402/403/429. Field names follow each provider's documentation;
see docs/API_AND_ENDPOINTS.md for what is verified.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.search import SearchResult
from app.orchestrator import quota

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30, connect=10)
_TBS = {"pd": "qdr:d", "pw": "qdr:w", "pm": "qdr:m", "py": "qdr:y"}
_TAVILY_RANGE = {"pd": "day", "pw": "week", "pm": "month", "py": "year"}
# Tavily boosts by country name, not code.
_COUNTRY_NAMES = {
    "US": "united states", "GB": "united kingdom", "DE": "germany", "FR": "france", "ES": "spain",
    "IT": "italy", "NL": "netherlands", "IE": "ireland", "CA": "canada", "AU": "australia",
    "NZ": "new zealand", "SE": "sweden", "NO": "norway", "DK": "denmark", "FI": "finland",
    "PL": "poland", "PT": "portugal", "BE": "belgium", "AT": "austria", "CH": "switzerland",
}  # fmt: skip


def _gl(country: str | None) -> str | None:
    if not country:
        return None
    return "gb" if country.upper() in ("UK", "GB") else country.lower()


def _results(items: Any, *, title: str, url: str, snippet: str, date: str) -> list[SearchResult]:
    out = []
    for r in items or []:
        if isinstance(r, dict) and r.get(url):
            out.append(
                SearchResult(
                    title=str(r.get(title) or ""),
                    url=str(r[url]),
                    description=str(r.get(snippet) or "")[:1000],
                    age=r.get(date),
                )
            )
    return out


class _HttpSearch:
    """Shared plumbing: count usage, call, raise on HTTP errors (the router falls back)."""

    name: ClassVar[str]
    is_mock: ClassVar[bool] = False

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def _count(self, session: AsyncSession, agent: str, query: str) -> None:
        await quota.consume(session, self.name, agent=agent, units=1, description=query[:200])


class SerpApiSearch(_HttpSearch):
    """serpapi.com: GET /search?engine=google, key as `api_key`, results in `organic_results`."""

    name: ClassVar[str] = "serpapi"
    ENDPOINT = "https://serpapi.com/search"

    async def search(
        self, session: AsyncSession, query: str, *, agent: str, country: str | None = None,
        language: str | None = None, count: int = 20, freshness: str | None = None, page: int = 1,
    ) -> list[SearchResult]:  # fmt: skip
        await self._count(session, agent, query)
        params: dict[str, Any] = {"engine": "google", "q": query, "api_key": self._key}
        if gl := _gl(country):
            params["gl"] = gl
        if language:
            params["hl"] = language
        if freshness in _TBS:
            params["tbs"] = _TBS[freshness]
        if page > 1:
            params["start"] = (page - 1) * 10
        resp = await self._client.get(self.ENDPOINT, params=params)
        resp.raise_for_status()
        return _results(resp.json().get("organic_results"),
                        title="title", url="link", snippet="snippet", date="date")[:count]  # fmt: skip


class ScrapingDogSearch(_HttpSearch):
    """scrapingdog.com: GET /google/, key as `api_key`, results in `organic_results`.
    The formal docs page was unavailable; fields are from the provider's product page."""

    name: ClassVar[str] = "scrapingdog"
    ENDPOINT = "https://api.scrapingdog.com/google/"

    async def search(
        self, session: AsyncSession, query: str, *, agent: str, country: str | None = None,
        language: str | None = None, count: int = 20, freshness: str | None = None, page: int = 1,
    ) -> list[SearchResult]:  # fmt: skip
        await self._count(session, agent, query)
        params: dict[str, Any] = {
            "api_key": self._key, "query": query, "results": min(count, 10), "page": page - 1,
        }  # fmt: skip
        if gl := _gl(country):
            params["country"] = gl
        resp = await self._client.get(self.ENDPOINT, params=params)
        resp.raise_for_status()
        return _results(resp.json().get("organic_results"),
                        title="title", url="link", snippet="snippet", date="date")  # fmt: skip


class SearloSearch(_HttpSearch):
    """searlo.tech: GET /api/v1/search/web, key in the `x-api-key` header, results in `organic`."""

    name: ClassVar[str] = "searlo"
    ENDPOINT = "https://api.searlo.tech/api/v1/search/web"

    async def search(
        self, session: AsyncSession, query: str, *, agent: str, country: str | None = None,
        language: str | None = None, count: int = 20, freshness: str | None = None, page: int = 1,
    ) -> list[SearchResult]:  # fmt: skip
        await self._count(session, agent, query)
        params: dict[str, Any] = {"q": query, "limit": min(count, 10)}
        if gl := _gl(country):
            params["gl"] = gl
        if language:
            params["hl"] = language
        resp = await self._client.get(
            self.ENDPOINT, params=params, headers={"x-api-key": self._key}
        )
        resp.raise_for_status()
        return _results(resp.json().get("organic"),
                        title="title", url="link", snippet="snippet", date="date")  # fmt: skip


class TavilySearch(_HttpSearch):
    """tavily.com: POST /search with a Bearer key, results in `results` (title, url, content)."""

    name: ClassVar[str] = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    async def search(
        self, session: AsyncSession, query: str, *, agent: str, country: str | None = None,
        language: str | None = None, count: int = 20, freshness: str | None = None, page: int = 1,
    ) -> list[SearchResult]:  # fmt: skip
        await self._count(session, agent, query)
        body: dict[str, Any] = {
            "query": query, "max_results": min(count, 20), "include_published_date": True,
        }  # fmt: skip
        if country and (name := _COUNTRY_NAMES.get(country.upper())):
            body["country"] = name
        if freshness in _TAVILY_RANGE:
            body["time_range"] = _TAVILY_RANGE[freshness]
        resp = await self._client.post(
            self.ENDPOINT, json=body, headers={"Authorization": f"Bearer {self._key}"}
        )
        resp.raise_for_status()
        return _results(resp.json().get("results"),
                        title="title", url="url", snippet="content", date="published_date")  # fmt: skip
