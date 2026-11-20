"""Open business data for no-website / outdated-website discovery (no API keys):
OpenStreetMap via Overpass, France's recherche-entreprises, Norway's Brønnøysund register
(brreg) and the UK Companies House advanced search.

Every result is a `FoundBusiness` with a website status:
- OSM_WEBSITE_MISSING: OpenStreetMap lists the business without any website tag
- WEBSITE_RECORDED: the source records a website
- WEBSITE_DISCOVERED: a domain for the name was found by our own check
- WEBSITE_VERIFIED: that domain's page names the business
- WEBSITE_UNCONFIRMED: the source has no website field (registries), nothing checked yet
Data from OpenStreetMap is © OpenStreetMap contributors (ODbL); the attribution is stored with
every OSM result and shown wherever it appears.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)
UA = {"User-Agent": "LeadsSystemBot/0.1 (business search; respects usage policies)"}


class WebsiteStatus(StrEnum):
    OSM_WEBSITE_MISSING = "OSM_WEBSITE_MISSING"
    WEBSITE_RECORDED = "WEBSITE_RECORDED"
    WEBSITE_DISCOVERED = "WEBSITE_DISCOVERED"
    WEBSITE_VERIFIED = "WEBSITE_VERIFIED"
    WEBSITE_UNCONFIRMED = "WEBSITE_UNCONFIRMED"


@dataclass(slots=True)
class FoundBusiness:
    source: str  # osm | fr_recherche | no_brreg | companies_house
    source_id: str
    name: str
    country: str
    website_status: WebsiteStatus
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    url: str | None = None  # the public record
    category: str | None = None
    attribution: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class OpenDataError(Exception):
    pass


# Friendly category -> OSM tags, French search term, Norwegian industry group (SN2025, checked
# against brreg on 2026-11-21; a group code matches all its sub-codes) and UK SIC codes.
CATEGORIES: dict[str, dict[str, Any]] = {
    "plumber": {"osm": [("craft", "plumber")], "fr": "plombier", "no": "43.22", "sic": ["43220"]},
    "electrician": {"osm": [("craft", "electrician")], "fr": "électricien", "no": "43.21", "sic": ["43210"]},
    "carpenter": {"osm": [("craft", "carpenter")], "fr": "menuisier", "no": "43.32", "sic": ["43320"]},
    "painter": {"osm": [("craft", "painter")], "fr": "peintre en bâtiment", "no": "43.34", "sic": ["43341"]},
    "roofer": {"osm": [("craft", "roofer")], "fr": "couvreur", "no": "43.91", "sic": ["43910"]},
    "hvac": {"osm": [("craft", "hvac")], "fr": "chauffagiste", "no": "43.22", "sic": ["43220"]},
    "gardener": {"osm": [("craft", "gardener")], "fr": "paysagiste", "no": "81.30", "sic": ["81300"]},
    "locksmith": {"osm": [("shop", "locksmith"), ("craft", "locksmith")], "fr": "serrurier", "no": "95.29", "sic": ["95250"]},
    "bakery": {"osm": [("shop", "bakery")], "fr": "boulangerie", "no": "10.71", "sic": ["10710", "47240"]},
    "hairdresser": {"osm": [("shop", "hairdresser")], "fr": "coiffure", "no": "96.21", "sic": ["96020"]},
    "beauty": {"osm": [("shop", "beauty")], "fr": "institut de beauté", "no": "96.22", "sic": ["96020"]},
    "florist": {"osm": [("shop", "florist")], "fr": "fleuriste", "no": "47.76", "sic": ["47760"]},
    "restaurant": {"osm": [("amenity", "restaurant")], "fr": "restaurant", "no": "56.11", "sic": ["56101"]},
    "cafe": {"osm": [("amenity", "cafe")], "fr": "café", "no": "56.30", "sic": ["56102"]},
    "dentist": {"osm": [("amenity", "dentist")], "fr": "dentiste", "no": "86.23", "sic": ["86230"]},
    "car_repair": {"osm": [("shop", "car_repair")], "fr": "garage automobile", "no": "95.31", "sic": ["45200"]},
    "photographer": {"osm": [("craft", "photographer"), ("shop", "photo")], "fr": "photographe", "no": "74.20", "sic": ["74201"]},
    "cleaning": {"osm": [("office", "cleaning"), ("craft", "cleaning")], "fr": "nettoyage", "no": "81.21", "sic": ["81210"]},
}  # fmt: skip

# Country -> sources tried, in order (OpenStreetMap everywhere; a registry where one is free).
ROUTES: dict[str, tuple[str, ...]] = {
    "GB": ("osm", "companies_house"),
    "FR": ("osm", "fr_recherche"),
    "NO": ("osm", "no_brreg"),
}
DEFAULT_ROUTE: tuple[str, ...] = ("osm",)


def route_for(country: str) -> tuple[str, ...]:
    return ROUTES.get(country.upper(), DEFAULT_ROUTE)


def _website_tag(tags: dict[str, str]) -> str | None:
    for k in ("website", "contact:website", "url", "contact:url", "brand:website"):
        if tags.get(k):
            return tags[k]
    return None


class _RateLimiter:
    """At most `per_minute` calls in any 60 s window (shared by the process)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, per_minute)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= 60:
                self._calls.popleft()
            if len(self._calls) >= self.per_minute:
                await asyncio.sleep(60 - (now - self._calls[0]))
            self._calls.append(time.monotonic())


_OVERPASS_LIMIT: _RateLimiter | None = None


class Overpass:
    """OpenStreetMap through the Overpass API, mirrors tried in order on 429/5xx/timeouts."""

    def __init__(
        self, urls: list[str], *, per_minute: int = 2, attribution: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:  # fmt: skip
        global _OVERPASS_LIMIT  # noqa: PLW0603 - one limit for the whole process
        if _OVERPASS_LIMIT is None or _OVERPASS_LIMIT.per_minute != per_minute:
            _OVERPASS_LIMIT = _RateLimiter(per_minute)
        self.limit = _OVERPASS_LIMIT
        self.urls = [u for u in urls if u]
        self.attribution = attribution or "© OpenStreetMap contributors (ODbL)"
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(70, connect=10), headers=UA
        )

    @staticmethod
    def query(country: str, city: str, category: str, limit: int) -> str:
        tags = CATEGORIES.get(category, {}).get("osm") or []
        if not tags and "=" in category:  # raw OSM tag such as "shop=tailor"
            k, _, v = category.partition("=")
            tags = [(k.strip(), v.strip())]
        if not tags:
            raise OpenDataError(f"unknown category {category!r}")
        safe_city = city.replace('"', "")
        parts = "".join(f'nwr["{k}"="{v}"]["name"](area.a);' for k, v in tags)
        return (
            f"[out:json][timeout:60];"
            f'area["ISO3166-1"="{country.upper()}"][admin_level=2]->.c;'
            f'area["name"="{safe_city}"]["boundary"="administrative"](area.c)->.a;'
            f"({parts});out center tags {int(limit)};"
        )

    async def search(
        self, country: str, city: str, category: str, limit: int = 50
    ) -> list[FoundBusiness]:
        q = self.query(country, city, category, limit)
        last = ""
        for url in self.urls:
            await self.limit.wait()
            try:
                resp = await self._client.post(url, data={"data": q})
            except httpx.HTTPError as exc:
                last = f"{url}: {type(exc).__name__}"
                continue
            if resp.status_code == 200:
                return self.parse(resp.json(), country, category)
            last = f"{url}: HTTP {resp.status_code}"
        raise OpenDataError(f"Overpass unavailable ({last})")

    def parse(self, body: dict[str, Any], country: str, category: str) -> list[FoundBusiness]:
        out = []
        for el in body.get("elements") or []:
            tags = el.get("tags") or {}
            if not tags.get("name"):
                continue
            centre = el.get("center") or {}
            site = _website_tag(tags)
            street = " ".join(
                x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x
            )
            addr = ", ".join(
                x for x in (street, tags.get("addr:postcode"), tags.get("addr:city")) if x
            )
            out.append(FoundBusiness(
                source="osm", source_id=f"{el.get('type')}/{el.get('id')}", name=tags["name"],
                country=(tags.get("addr:country") or country).upper()[:2],
                website_status=WebsiteStatus.WEBSITE_RECORDED if site else WebsiteStatus.OSM_WEBSITE_MISSING,
                website=site, phone=tags.get("phone") or tags.get("contact:phone"),
                email=tags.get("email") or tags.get("contact:email"), address=addr or None,
                city=tags.get("addr:city"), lat=el.get("lat") or centre.get("lat"),
                lon=el.get("lon") or centre.get("lon"),
                url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                category=category, attribution=self.attribution, raw={"tags": tags},
            ))  # fmt: skip
        return out


class FrRechercheEntreprises:
    """recherche-entreprises.api.gouv.fr: active companies by text search (max 25 per page)."""

    def __init__(self, base_url: str, *, max_rps: int = 5, client: httpx.AsyncClient | None = None):
        self.base = base_url.rstrip("/")
        self._gap = 1 / max(1, max_rps)
        self._client = client or httpx.AsyncClient(timeout=30, headers=UA)

    async def search(self, city: str, category: str, limit: int = 25) -> list[FoundBusiness]:
        term = CATEGORIES.get(category, {}).get("fr") or category
        out: list[FoundBusiness] = []
        page = 1
        while len(out) < limit:
            resp = await self._client.get(f"{self.base}/search", params={
                "q": f"{term} {city}", "per_page": min(25, limit), "page": page,
                "etat_administratif": "A",
            })  # fmt: skip
            if resp.status_code >= 400:
                raise OpenDataError(f"recherche-entreprises HTTP {resp.status_code}")
            body = resp.json()
            out += self.parse(body, category)
            if page >= int(body.get("total_pages") or 1):
                break
            page += 1
            await asyncio.sleep(self._gap)
        return out[:limit]

    @staticmethod
    def parse(body: dict[str, Any], category: str) -> list[FoundBusiness]:
        out = []
        for r in body.get("results") or []:
            siren, name = r.get("siren"), r.get("nom_complet") or r.get("nom_raison_sociale")
            if not siren or not name:
                continue
            siege = r.get("siege") or {}
            addr = ", ".join(x for x in (siege.get("adresse"), siege.get("code_postal"),
                                         siege.get("libelle_commune")) if x)  # fmt: skip
            out.append(FoundBusiness(
                source="fr_recherche", source_id=str(siren), name=str(name), country="FR",
                website_status=WebsiteStatus.WEBSITE_UNCONFIRMED, address=addr or None,
                city=siege.get("libelle_commune"),
                lat=float(siege["latitude"]) if siege.get("latitude") else None,
                lon=float(siege["longitude"]) if siege.get("longitude") else None,
                url=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}",
                category=category,
                raw={"naf": r.get("activite_principale"), "created": r.get("date_creation")},
            ))  # fmt: skip
        return out


class NoBrreg:
    """data.brreg.no Enhetsregisteret: units by name or industry code; records `hjemmeside`."""

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=30, headers=UA)

    async def search(self, city: str, category: str, limit: int = 50) -> list[FoundBusiness]:
        code = CATEGORIES.get(category, {}).get("no")
        params: dict[str, Any] = {"size": min(100, limit), "konkurs": "false"}
        if code:
            params["naeringskode"] = code
        else:
            params["navn"] = category
        if city:
            params["forretningsadresse.poststed"] = city.upper()
        resp = await self._client.get(f"{self.base}/enheter", params=params)
        if resp.status_code >= 400:
            raise OpenDataError(f"brreg HTTP {resp.status_code}")
        return self.parse(resp.json(), category)[:limit]

    @staticmethod
    def parse(body: dict[str, Any], category: str) -> list[FoundBusiness]:
        out = []
        for e in (body.get("_embedded") or {}).get("enheter") or []:
            orgnr, name = e.get("organisasjonsnummer"), e.get("navn")
            if not orgnr or not name:
                continue
            a = e.get("forretningsadresse") or e.get("postadresse") or {}
            addr = ", ".join(
                x for x in (*(a.get("adresse") or []), a.get("postnummer"), a.get("poststed")) if x
            )
            site = e.get("hjemmeside")
            out.append(FoundBusiness(
                source="no_brreg", source_id=str(orgnr), name=str(name),
                country=str(a.get("landkode") or "NO")[:2],
                website_status=WebsiteStatus.WEBSITE_RECORDED if site else WebsiteStatus.WEBSITE_UNCONFIRMED,
                website=site, phone=e.get("telefon") or e.get("mobil"), email=e.get("epostadresse"),
                address=addr or None, city=a.get("poststed"),
                url=f"https://virksomhet.brreg.no/oppslag/enheter/{orgnr}", category=category,
                raw={"nace": (e.get("naeringskode1") or {}).get("kode"),
                     "registered": e.get("registreringsdatoEnhetsregisteret")},
            ))  # fmt: skip
        return out


class CompaniesHouseSearch:
    """UK Companies House advanced search: active companies by SIC code and location."""

    ENDPOINT = "https://api.company-information.service.gov.uk/advanced-search/companies"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=30)

    async def search(self, city: str, category: str, limit: int = 50) -> list[FoundBusiness]:
        sic = CATEGORIES.get(category, {}).get("sic")
        if not sic:
            return []
        resp = await self._client.get(self.ENDPOINT, auth=(self._key, ""), params={
            "sic_codes": ",".join(sic), "location": city, "company_status": "active",
            "size": str(min(100, limit)),
        })  # fmt: skip
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise OpenDataError(f"Companies House HTTP {resp.status_code}")
        return self.parse(resp.json(), category)[:limit]

    @staticmethod
    def parse(body: dict[str, Any], category: str) -> list[FoundBusiness]:
        out = []
        for item in body.get("items") or []:
            number, name = item.get("company_number"), item.get("company_name")
            if not number or not name:
                continue
            a = item.get("registered_office_address") or {}
            addr = ", ".join(
                str(a[k]) for k in ("address_line_1", "locality", "postal_code") if a.get(k)
            )
            out.append(FoundBusiness(
                source="companies_house", source_id=str(number), name=str(name), country="GB",
                website_status=WebsiteStatus.WEBSITE_UNCONFIRMED, address=addr or None,
                city=a.get("locality"),
                url=f"https://find-and-update.company-information.service.gov.uk/company/{number}",
                category=category,
                raw={"sic": item.get("sic_codes"), "created": item.get("date_of_creation")},
            ))  # fmt: skip
        return out


GEOFABRIK_APPROVAL_MB = 300


async def geofabrik_size_mb(
    base_url: str, region_path: str, client: httpx.AsyncClient | None = None
) -> float | None:
    """Size of a Geofabrik extract (HEAD request only; nothing is downloaded)."""
    c = client or httpx.AsyncClient(timeout=30, headers=UA)
    resp = await c.head(
        f"{base_url.rstrip('/')}/{region_path.strip('/')}-latest.osm.pbf", follow_redirects=True
    )
    if resp.status_code != 200 or not resp.headers.get("content-length"):
        return None
    return round(int(resp.headers["content-length"]) / 1_000_000, 1)
