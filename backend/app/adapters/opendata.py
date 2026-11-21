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
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def bot_user_agent(contact: str = "") -> str:
    """Identifying User-Agent; with a contact address when one is configured, as the Overpass
    and Nominatim usage policies ask."""
    who = f"; contact: {contact.strip()}" if contact.strip() else ""
    return f"LeadsSystemBot/0.1 (business search; respects usage policies{who})"


UA = {"User-Agent": bot_user_agent()}


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

# Answers that mean "this mirror is busy or slow right now", not "the query is wrong".
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
# Overpass reports a query that ran out of time or memory with HTTP 200 and a remark; the
# elements are then incomplete, so the answer is treated like a timeout.
_RUNTIME_ERROR = re.compile(r"runtime error|timed out|out of memory", re.I)

# The weekly sweep: named shops, offices, crafts and these amenities.
SWEEP_KEYS: tuple[str, ...] = ("shop", "office", "craft")
SWEEP_AMENITIES: tuple[str, ...] = (
    "restaurant", "cafe", "bar", "fast_food", "dentist", "clinic", "doctors", "pharmacy",
    "beauty", "veterinary", "bank",
)  # fmt: skip
# Offices that are not businesses buying a website: embassies, public bodies, parties,
# charities and places of worship.
SWEEP_SKIP_OFFICES: frozenset[str] = frozenset(
    {"government", "diplomatic", "political_party", "religion", "ngo", "association", "charity"}
)
PHONE_KEYS: tuple[str, ...] = ("phone", "contact:phone", "mobile", "contact:mobile")
EMAIL_KEYS: tuple[str, ...] = ("email", "contact:email")


def _first(tags: dict[str, str], keys: tuple[str, ...]) -> str | None:
    """First value of the first key present; OSM separates several values with ';'."""
    for k in keys:
        v = (tags.get(k) or "").split(";")[0].strip()
        if v:
            return v
    return None


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(0.0, float(resp.headers.get("retry-after", "0")))
    except ValueError:
        return 0.0


class Overpass:
    """OpenStreetMap through the Overpass API. Mirrors are tried in order; when every mirror is
    busy (429/5xx/timeouts) the whole list is tried again after a backoff with jitter, up to
    `rounds` passes."""

    def __init__(
        self, urls: list[str], *, per_minute: int = 2, attribution: str = "",
        client: httpx.AsyncClient | None = None, contact: str = "", rounds: int = 1,
        backoff_s: float = 10.0, max_backoff_s: float = 120.0, timeout_s: float = 70.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:  # fmt: skip
        global _OVERPASS_LIMIT  # noqa: PLW0603 - one limit for the whole process
        if _OVERPASS_LIMIT is None or _OVERPASS_LIMIT.per_minute != per_minute:
            _OVERPASS_LIMIT = _RateLimiter(per_minute)
        self.limit = _OVERPASS_LIMIT
        self.urls = [u for u in urls if u]
        self.attribution = attribution or "© OpenStreetMap contributors (ODbL)"
        self.rounds = max(1, rounds)
        self.backoff_s, self.max_backoff_s = backoff_s, max_backoff_s
        self._sleep = sleep
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10),
            headers={"User-Agent": bot_user_agent(contact)},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

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

    @staticmethod
    def sweep_query(lat: float, lon: float, radius_m: int, timeout_s: int = 90) -> str:
        """Named shops/offices/crafts and the listed amenities within `radius_m` of a point,
        only those with a phone number or an email address (the website split is done on the
        answer, so one query serves both groups)."""
        around = f"(around:{int(radius_m)},{lat:.5f},{lon:.5f})"
        contact = '[~"^(' + "|".join(PHONE_KEYS + EMAIL_KEYS) + ')$"~"."]'
        amenity = '["amenity"~"^(' + "|".join(SWEEP_AMENITIES) + ')$"]'
        parts = [f'nwr["{k}"]["name"]{contact}{around};' for k in SWEEP_KEYS]
        parts.append(f'nwr{amenity}["name"]{contact}{around};')
        return f"[out:json][timeout:{int(timeout_s)}];({''.join(parts)});out center tags;"

    def _backoff(self, rnd: int, retry_after: float) -> float:
        base = min(
            self.max_backoff_s, max(retry_after, self.backoff_s * float(2 ** max(0, rnd - 1)))
        )
        return base + random.uniform(0, self.backoff_s)  # noqa: S311 - jitter, not security

    async def run(self, q: str) -> tuple[dict[str, Any], str]:
        """Answer of the first mirror that answers cleanly, and that mirror's URL."""
        last, retry_after = "", 0.0
        for rnd in range(self.rounds):
            if rnd:
                await self._sleep(self._backoff(rnd, retry_after))
                retry_after = 0.0
            for url in self.urls:
                await self.limit.wait()
                try:
                    resp = await self._client.post(url, data={"data": q})
                except httpx.HTTPError as exc:
                    last = f"{url}: {type(exc).__name__}"
                    continue
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        last = f"{url}: answer is not JSON"
                        continue
                    remark = str(body.get("remark") or "")
                    if _RUNTIME_ERROR.search(remark):
                        last = f"{url}: {remark[:160]}"
                        continue
                    return body, url
                if resp.status_code == 400:  # the query itself is wrong: no mirror will take it
                    raise OpenDataError(f"Overpass rejected the query ({url}: HTTP 400)")
                if resp.status_code == 429:
                    retry_after = max(retry_after, _retry_after(resp))
                last = f"{url}: HTTP {resp.status_code}"
        raise OpenDataError(f"Overpass unavailable ({last})")

    async def search(
        self, country: str, city: str, category: str, limit: int = 50
    ) -> list[FoundBusiness]:
        body, _ = await self.run(self.query(country, city, category, limit))
        return self.parse(body, country, category)

    async def sweep(
        self, lat: float, lon: float, radius_m: int, *, country: str, city: str
    ) -> tuple[list[FoundBusiness], str, int]:
        """One sweep city: the kept businesses, the mirror that answered and the raw element
        count."""
        body, url = await self.run(self.sweep_query(lat, lon, radius_m))
        return self.parse_sweep(body, country, city), url, len(body.get("elements") or [])

    def _found(
        self, el: dict[str, Any], tags: dict[str, str], country: str, category: str,
        city: str | None = None,
    ) -> FoundBusiness:  # fmt: skip
        centre = el.get("center") or {}
        site = _website_tag(tags)
        street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
        addr = ", ".join(x for x in (street, tags.get("addr:postcode"), tags.get("addr:city")) if x)
        own = (tags.get("addr:country") or "").strip().upper()
        return FoundBusiness(
            source="osm", source_id=f"{el.get('type')}/{el.get('id')}", name=tags["name"],
            country=(own if len(own) == 2 and own.isalpha() else country.upper())[:2],
            website_status=WebsiteStatus.WEBSITE_RECORDED if site else WebsiteStatus.OSM_WEBSITE_MISSING,
            website=site, phone=_first(tags, PHONE_KEYS), email=_first(tags, EMAIL_KEYS),
            address=addr or None, city=tags.get("addr:city") or city,
            lat=el.get("lat") or centre.get("lat"), lon=el.get("lon") or centre.get("lon"),
            url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
            category=category, attribution=self.attribution, raw={"tags": tags},
        )  # fmt: skip

    def parse(self, body: dict[str, Any], country: str, category: str) -> list[FoundBusiness]:
        out = []
        for el in body.get("elements") or []:
            tags = el.get("tags") or {}
            if tags.get("name"):
                out.append(self._found(el, tags, country, category))
        return out

    def parse_sweep(self, body: dict[str, Any], country: str, city: str) -> list[FoundBusiness]:
        """Named businesses with a phone or email. Branches of chains (a `brand:wikidata` tag)
        are skipped: their website and marketing are run centrally, so they are not
        prospects."""
        out = []
        for el in body.get("elements") or []:
            tags = el.get("tags") or {}
            if not tags.get("name") or tags.get("brand:wikidata"):
                continue
            if tags.get("office") in SWEEP_SKIP_OFFICES:
                continue
            if not (_first(tags, PHONE_KEYS) or _first(tags, EMAIL_KEYS)):
                continue
            category = sweep_category(tags)
            if category is None:
                continue
            out.append(self._found(el, tags, country, category, city))
        return out


def sweep_category(tags: dict[str, str]) -> str | None:
    """The tag that put an element in the sweep, as key=value."""
    if tags.get("amenity") in SWEEP_AMENITIES:
        return f"amenity={tags['amenity']}"
    for k in ("craft", "office", "shop"):
        if tags.get(k):
            return f"{k}={tags[k]}"
    return None


def osm_ref(f: FoundBusiness) -> tuple[str, int] | None:
    """(osm_type, osm_id) of an OpenStreetMap result."""
    if f.source != "osm":
        return None
    kind, _, num = f.source_id.partition("/")
    if kind not in ("node", "way", "relation") or not num.isdigit():
        return None
    return kind, int(num)


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
