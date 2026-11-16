"""Free-tier search providers: Serper (Google results via API) and Exa (fallback).

Never scrapes Google result pages. Every call is counted against the
provider's free quota first (orchestrator.quota).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.adapters.google import PlaceResult
from app.adapters.search import SearchAdapter, SearchResult
from app.orchestrator import quota
from app.orchestrator.budget import BudgetExceededError

logger = logging.getLogger(__name__)

_FRESHNESS_TBS = {"pd": "qdr:d", "pw": "qdr:w", "pm": "qdr:m", "py": "qdr:y"}
_FRESHNESS_DAYS = {"pd": 1, "pw": 7, "pm": 31, "py": 365}


def _gl(country: str | None) -> str | None:
    if not country:
        return None
    return "gb" if country.upper() in ("UK", "GB") else country.lower()


class SerperSearch:
    """Serper.dev: Google web + local results through a licensed API. 1 credit per query
    (num ≤ 10). The live balance is read from /account (free) for the Sources page."""

    name: ClassVar[str] = "serper"
    is_mock: ClassVar[bool] = False
    BASE = "https://google.serper.dev"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=30)

    async def _post(
        self, session: AsyncSession, path: str, body: dict[str, Any], *, agent: str
    ) -> dict[str, Any]:
        await quota.consume(
            session, "serper", agent=agent, units=1, description=f"{path}: {body.get('q')}"[:200]
        )
        resp = await self._client.post(f"{self.BASE}/{path}", json=body, headers=self._headers)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        agent: str,
        country: str | None = None,
        language: str | None = None,
        count: int = 20,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        body: dict[str, Any] = {"q": query, "num": min(count, 10)}
        if gl := _gl(country):
            body["gl"] = gl
        if language:
            body["hl"] = language
        if freshness in _FRESHNESS_TBS:
            body["tbs"] = _FRESHNESS_TBS[freshness]
        data = await self._post(session, "search", body, agent=agent)
        return [
            SearchResult(
                title=str(r.get("title", "")),
                url=str(r["link"]),
                description=str(r.get("snippet", "")),
                age=r.get("date"),
            )
            for r in data.get("organic") or []
            if isinstance(r, dict) and r.get("link")
        ]

    async def places(
        self, session: AsyncSession, query: str, *, agent: str, country: str | None = None
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"q": query}
        if gl := _gl(country):
            body["gl"] = gl
        data = await self._post(session, "places", body, agent=agent)
        return [p for p in data.get("places") or [] if isinstance(p, dict)]

    async def credits(self) -> int | None:
        """Live remaining credit balance (consumes no credit)."""
        try:
            resp = await self._client.get(f"{self.BASE}/account", headers=self._headers)
            resp.raise_for_status()
            return int(resp.json().get("balance"))
        except (httpx.HTTPError, TypeError, ValueError):
            return None


class SerperPlaces:
    """Single-business local lookups via Serper's Google local results (Google Places API is
    off in testing mode). One business per call — never bulk collection."""

    name: ClassVar[str] = "serper_places"
    is_mock: ClassVar[bool] = False

    def __init__(self, serper: SerperSearch) -> None:
        self._serper = serper

    async def lookup(
        self, session: AsyncSession, query: str, *, agent: str, region: str | None = None
    ) -> PlaceResult | None:
        places = await self._serper.places(session, query, agent=agent, country=region)
        if not places:
            return None
        p = places[0]
        cid = str(p.get("cid") or p.get("placeId") or p.get("title", ""))
        return PlaceResult(
            place_id=cid,
            name=str(p.get("title", "")),
            address=p.get("address"),
            website=p.get("website"),
            phone=p.get("phoneNumber"),
            business_status=None,
            maps_url=f"https://maps.google.com/?cid={p['cid']}" if p.get("cid") else None,
            rating=float(p["rating"]) if p.get("rating") is not None else None,
            rating_count=int(p["ratingCount"]) if p.get("ratingCount") is not None else None,
        )


class ExaSearch:
    """Exa /search, used as the search fallback. Request shape follows the Exa skill:
    query + type auto + contents.highlights, plus `objective` (one step of the lead pipeline)
    and a publish-date window only when the caller asks for fresh results."""

    name: ClassVar[str] = "exa"
    is_mock: ClassVar[bool] = False
    ENDPOINT = "https://api.exa.ai/search"
    OBJECTIVE = (
        "Find pages where a small business or individual in the US or UK asks for, or clearly "
        "needs, a logo, a new website, a website redesign or SEO help. Rank first posts and "
        "listings written by the person who needs the work; exclude agencies and freelancers "
        "advertising their own services, tutorials and listicles. Pull who is asking, what "
        "they need, their location and any budget mentioned."
    )

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        self._client = client or httpx.AsyncClient(timeout=60)

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        agent: str,
        country: str | None = None,
        language: str | None = None,
        count: int = 20,
        freshness: str | None = None,
        objective: str | None = None,
    ) -> list[SearchResult]:
        # Reserve a conservative estimate; settle with Exa's reported cost.
        reserved = await quota.consume(
            session, "exa", agent=agent, units=1, credit_usd=0.01, description=query[:200]
        )
        body: dict[str, Any] = {
            "query": query,
            "type": "auto",
            "contents": {"highlights": True},
            "objective": objective or self.OBJECTIVE,
        }
        if country:
            body["userLocation"] = "GB" if country.upper() == "UK" else country.upper()
        if freshness in _FRESHNESS_DAYS:
            start = datetime.now(UTC) - timedelta(days=_FRESHNESS_DAYS[freshness])
            body["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        resp = await self._client.post(self.ENDPOINT, json=body, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        cost = float((data.get("costDollars") or {}).get("total") or 0.0)
        await quota.settle(session, reserved, units=1, credit_usd=cost)
        out = []
        for r in data.get("results", []):
            if not r.get("url"):
                continue
            highlights = r.get("highlights") or []
            out.append(
                SearchResult(
                    title=str(r.get("title") or ""),
                    url=str(r["url"]),
                    description=" … ".join(str(h) for h in highlights)[:1000],
                    age=r.get("publishedDate"),
                )
            )
        return out


class FallbackSearch:
    """Primary search with automatic fallback (Serper → Exa) on errors or an exhausted quota."""

    is_mock: ClassVar[bool] = False

    def __init__(self, adapters: list[SearchAdapter]) -> None:
        if not adapters:
            raise ValueError("FallbackSearch needs at least one adapter")
        self.adapters = adapters
        self.name = "+".join(a.name for a in adapters)

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        agent: str,
        country: str | None = None,
        language: str | None = None,
        count: int = 20,
        freshness: str | None = None,
    ) -> list[SearchResult]:
        last: Exception | None = None
        for adapter in self.adapters:
            try:
                async with session.begin_nested():
                    return await adapter.search(
                        session,
                        query,
                        agent=agent,
                        country=country,
                        language=language,
                        count=count,
                        freshness=freshness,
                    )
            except (httpx.HTTPError, BudgetExceededError, AdapterUnavailableError) as exc:
                logger.warning("search via %s failed (%s); falling back", adapter.name, exc)
                last = exc
        assert last is not None
        raise last
