"""Google adapters: PageSpeed Insights (free) and Places API (New) — individual lookups only.

Places terms: no bulk database, limited fields, respect storage terms. We request a minimal
field mask, look up one named business at a time, and store only what the audit/trigger needs
(place id + website presence + profile URL) alongside the evidence record.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.orchestrator import budget
from app.services import runtime_settings

LIGHTHOUSE_CATEGORIES = ("performance", "accessibility", "best-practices", "seo")


class PageSpeedResult(BaseModel):
    url: str
    strategy: str
    scores: dict[str, float]  # 0–100
    metrics: dict[str, float]  # ms / unitless
    raw_audits: dict[str, Any] = {}


class PlaceResult(BaseModel):
    place_id: str
    name: str
    address: str | None = None
    website: str | None = None
    phone: str | None = None
    business_status: str | None = None
    maps_url: str | None = None
    rating: float | None = None
    rating_count: int | None = None


class PageSpeedAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def run(
        self, url: str, strategy: str = "mobile", *, session: AsyncSession | None = None
    ) -> PageSpeedResult: ...


class PlacesAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def lookup(
        self, session: AsyncSession, query: str, *, agent: str, region: str | None = None
    ) -> PlaceResult | None: ...


def parse_pagespeed(url: str, strategy: str, body: dict[str, Any]) -> PageSpeedResult:
    lh = body.get("lighthouseResult", {})
    cats = lh.get("categories", {})
    audits = lh.get("audits", {})
    scores = {
        c: round(float(cats[c]["score"]) * 100, 1)
        for c in LIGHTHOUSE_CATEGORIES
        if c in cats and cats[c].get("score") is not None
    }
    metric_ids = {
        "lcp_ms": "largest-contentful-paint",
        "fcp_ms": "first-contentful-paint",
        "tbt_ms": "total-blocking-time",
        "cls": "cumulative-layout-shift",
        "speed_index_ms": "speed-index",
    }
    metrics = {
        name: float(audits[a]["numericValue"])
        for name, a in metric_ids.items()
        if a in audits and "numericValue" in audits[a]
    }
    return PageSpeedResult(url=url, strategy=strategy, scores=scores, metrics=metrics)


class GooglePageSpeed:
    name: ClassVar[str] = "pagespeed"
    is_mock: ClassVar[bool] = False
    ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=120)

    async def run(
        self, url: str, strategy: str = "mobile", *, session: AsyncSession | None = None
    ) -> PageSpeedResult:
        key = None
        if session is not None:
            from datetime import timedelta  # noqa: PLC0415

            from app.orchestrator import quota  # noqa: PLC0415
            from app.services import cache  # noqa: PLC0415

            key = cache.make_key("pagespeed", {"url": url, "strategy": strategy})
            hit = await cache.get(session, key)
            if hit is not None:
                return PageSpeedResult.model_validate(hit)
            await quota.consume(session, "pagespeed", agent="audit", description=url[:200])
        pairs = [("url", url), ("strategy", strategy), ("key", self._key)]
        pairs += [("category", c.upper().replace("-", "_")) for c in LIGHTHOUSE_CATEGORIES]
        resp = await self._client.get(self.ENDPOINT, params=tuple(pairs))
        resp.raise_for_status()
        result = parse_pagespeed(url, strategy, resp.json())
        if session is not None and key is not None:
            await cache.put(
                session, key, "pagespeed", result.model_dump(mode="json"), ttl=timedelta(days=7)
            )
        return result


class GooglePlaces:
    name: ClassVar[str] = "places"
    is_mock: ClassVar[bool] = False
    ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
    FIELD_MASK = (
        "places.id,places.displayName,places.formattedAddress,places.websiteUri,"
        "places.businessStatus,places.googleMapsUri,places.nationalPhoneNumber,"
        "places.rating,places.userRatingCount"
    )

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=20)

    async def lookup(
        self, session: AsyncSession, query: str, *, agent: str, region: str | None = None
    ) -> PlaceResult | None:
        rs = await runtime_settings.load(session)
        await budget.charge(
            session,
            provider="places",
            agent=agent,
            amount_usd=rs.provider_prices.get("places", 0.035),
            description="place lookup",
        )
        payload: dict[str, Any] = {"textQuery": query, "pageSize": 1}
        if region:
            payload["regionCode"] = region
        resp = await self._client.post(
            self.ENDPOINT,
            json=payload,
            headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": self.FIELD_MASK},
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if not places:
            return None
        p = places[0]
        return PlaceResult(
            place_id=p["id"],
            name=p.get("displayName", {}).get("text", ""),
            address=p.get("formattedAddress"),
            website=p.get("websiteUri"),
            phone=p.get("nationalPhoneNumber"),
            business_status=p.get("businessStatus"),
            maps_url=p.get("googleMapsUri"),
            rating=p.get("rating"),
            rating_count=p.get("userRatingCount"),
        )


class MockPageSpeed:
    name: ClassVar[str] = "mock_pagespeed"
    is_mock: ClassVar[bool] = True

    async def run(
        self, url: str, strategy: str = "mobile", *, session: AsyncSession | None = None
    ) -> PageSpeedResult:
        return PageSpeedResult(
            url=url,
            strategy=strategy,
            scores={
                "performance": 41.0,
                "accessibility": 72.0,
                "best-practices": 67.0,
                "seo": 75.0,
            },
            metrics={"lcp_ms": 5200.0, "fcp_ms": 2900.0, "tbt_ms": 640.0, "cls": 0.21},
        )


class MockPlaces:
    name: ClassVar[str] = "mock_places"
    is_mock: ClassVar[bool] = True

    async def lookup(
        self, session: AsyncSession, query: str, *, agent: str, region: str | None = None
    ) -> PlaceResult | None:
        return PlaceResult(
            place_id="mock-place-1",
            name=query.split(",", maxsplit=1)[0],
            address="1 Example St",
            website=None,
            business_status="OPERATIONAL",
            rating=4.6,
            rating_count=38,
        )


class DisabledPageSpeed:
    name: ClassVar[str] = "pagespeed_disabled"
    is_mock: ClassVar[bool] = False

    async def run(
        self, url: str, strategy: str = "mobile", *, session: AsyncSession | None = None
    ) -> PageSpeedResult:
        raise AdapterUnavailableError("PageSpeed Insights", ("GOOGLE_PAGESPEED_API_KEY",))


class DisabledPlaces:
    name: ClassVar[str] = "places_disabled"
    is_mock: ClassVar[bool] = False

    async def lookup(
        self, session: AsyncSession, query: str, *, agent: str, region: str | None = None
    ) -> PlaceResult | None:
        raise AdapterUnavailableError("Google Places", ("GOOGLE_PLACES_API_KEY",))
