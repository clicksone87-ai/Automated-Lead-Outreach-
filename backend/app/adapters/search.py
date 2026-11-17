"""Search adapter for the Brave Search API."""

from __future__ import annotations

import hashlib
from typing import ClassVar, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.orchestrator import budget
from app.services import runtime_settings


class SearchResult(BaseModel):
    title: str
    url: str
    description: str = ""
    age: str | None = None


class SearchAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

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
        page: int = 1,
    ) -> list[SearchResult]: ...


class BraveSearch:
    name: ClassVar[str] = "brave"
    is_mock: ClassVar[bool] = False
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=20)

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
        page: int = 1,
    ) -> list[SearchResult]:
        rs = await runtime_settings.load(session)
        await budget.charge(
            session,
            provider="brave",
            agent=agent,
            amount_usd=rs.provider_prices.get("brave", 0.005),
            description=f"search: {query[:80]}",
        )
        params: dict[str, str | int] = {"q": query, "count": min(count, 20)}
        if country:
            params["country"] = "GB" if country.upper() == "UK" else country.upper()
        if language:
            params["search_lang"] = language
        if freshness:
            params["freshness"] = freshness
        resp = await self._client.get(
            self.ENDPOINT,
            params=params,
            headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r["url"],
                description=r.get("description", ""),
                age=r.get("age") or r.get("page_age"),
            )
            for r in results
            if r.get("url")
        ]


class MockSearch:
    name: ClassVar[str] = "mock_search"
    is_mock: ClassVar[bool] = True

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
        page: int = 1,
    ) -> list[SearchResult]:
        seed = hashlib.sha1(query.encode(), usedforsecurity=False).hexdigest()[:6]
        return [
            SearchResult(
                title=f"Need a new website for my plumbing business ({seed}-{i})",
                url=f"https://example.com/forum/{seed}-{i}",
                description="Looking for someone to build a simple website with booking. "
                "Budget around $1500.",
            )
            for i in range(min(count, 3))
        ]


class DisabledSearch:
    name: ClassVar[str] = "search_disabled"
    is_mock: ClassVar[bool] = False

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
        page: int = 1,
    ) -> list[SearchResult]:
        raise AdapterUnavailableError("Brave Search", ("BRAVE_SEARCH_API_KEY",))
