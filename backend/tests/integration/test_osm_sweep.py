"""OpenStreetMap weekly sweep against Postgres with recorded response shapes: GeoNames download
and load, per-day city records, pending retry and failure, upsert on (osm_type, osm_id) with
first_seen/last_seen, merging with registry records, excluded markets, no-website leads through
the ingest path (within the daily cap), the site-health hand-off and the API. No live calls."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from sqlalchemy import select

from app.adapters.fetcher import SafeFetcher
from app.adapters.opendata import Overpass, WebsiteStatus
from app.adapters.registry import Adapters, build_adapters
from app.agents.discovery.base import SourceContext
from app.agents.discovery.proactive_triggers.sources import SiteHealthSource
from app.config import Settings
from app.db.models import Business, Contact, Evidence, Lead, SweepCity, TriggerType
from app.db.session import session_scope
from app.services import geonames, osm_sweep, runtime_settings
from app.services.dedup import normalize_name
from tests.unit.test_osm_sweep_units import geo_line

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

MAIN = "https://overpass-main.test/api/interpreter"
MIRROR = "https://overpass-mirror.test/api/interpreter"
GEO_URL = "https://geonames.test/export/dump/cities15000.zip"
MONDAY = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)

GEO = "".join([
    geo_line(2950159, "Berlin", "DE", 3_426_354, "PPLC", 52.52437, 13.41053),
    geo_line(2911298, "Hamburg", "DE", 1_739_117, "PPLA", 53.57532, 10.01534),
    geo_line(2867714, "München", "DE", 1_260_391, "PPLA", 48.13743, 11.57549),
    geo_line(2988507, "Paris", "FR", 2_138_551, "PPLC", 48.85341, 2.3488),
    geo_line(2643743, "London", "GB", 8_961_989, "PPLC", 51.50853, -0.12574),
    geo_line(3042030, "Vaduz", "LI", 15_000, "PPLC", 47.14151, 9.52154),
    geo_line(1172451, "Lahore", "PK", 6_310_888, "PPLA", 31.558, 74.35071),
    geo_line(1275339, "Mumbai", "IN", 12_691_836, "PPLA", 19.07283, 72.88261),
])  # fmt: skip

BERLIN = {"elements": [
    {"type": "node", "id": 1, "lat": 52.5, "lon": 13.4, "tags": {
        "name": "Café Uno", "amenity": "cafe", "phone": "+49 30 1234567"}},
    {"type": "way", "id": 3, "center": {"lat": 52.51, "lon": 13.41}, "tags": {
        "name": "Bäckerei Drei", "shop": "bakery", "contact:email": "Info@Baeckerei-Drei.de"}},
    {"type": "node", "id": 2, "tags": {"name": "Dental Zwei", "amenity": "dentist",
                                       "phone": "+49 30 7654321",
                                       "website": "https://www.dentalzwei.de/"}},
    {"type": "node", "id": 4, "tags": {"name": "Chain Mart", "shop": "supermarket",
                                       "brand:wikidata": "Q1", "phone": "+49 30 1"}},
    {"type": "node", "id": 5, "tags": {"name": "Silent Carpentry", "craft": "carpenter"}},
    {"type": "node", "id": 6, "tags": {"name": "Grenzladen", "shop": "kiosk",
                                       "phone": "+92 42 1234567", "addr:country": "PK"}},
]}  # fmt: skip
HAMBURG = {"elements": [
    BERLIN["elements"][0],  # the same element again: same record, last_seen moves
    {"type": "node", "id": 51, "tags": {"name": "Pizza Roma", "amenity": "restaurant",
                                        "phone": "+49 40 999999"}},
]}  # fmt: skip


def _settings(tmp: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "demo_mode": True, "data_dir": tmp, "overpass_api_url": MAIN,
        "overpass_fallback_urls": MIRROR, "overpass_max_requests_per_minute": 1000,
        "osm_sweep_cities_per_country": 2, "geonames_cities_url": GEO_URL,
    }  # fmt: skip
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[call-arg]


def _zip_bytes(text: str = GEO) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cities15000.txt", text)
    return buf.getvalue()


async def _load_geo(tmp: Path) -> None:
    path = tmp / "cities.zip"
    path.write_bytes(_zip_bytes())
    async with session_scope() as s:
        await geonames.load(s, path, now=MONDAY)


async def _no_sleep(_: float) -> None:
    return None


def _overpass(settings: Settings) -> Overpass:
    return Overpass(settings.overpass_urls, per_minute=1000, rounds=1, sleep=_no_sleep)


def _city_router(answers: dict[str, Any]) -> Any:
    """Overpass answer by the city's latitude in the query."""

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(request.content.decode())["data"][0]
        for lat, answer in answers.items():
            if f",{lat}" in q:
                if isinstance(answer, int):  # an error status, a fresh response per call
                    return httpx.Response(answer, text="at capacity")
                return httpx.Response(200, json=answer)
        return httpx.Response(200, json={"elements": []})

    return handler


@respx.mock
async def test_geonames_refresh_downloads_loads_and_ranks_cities(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    route = respx.get(GEO_URL).mock(return_value=httpx.Response(200, content=_zip_bytes()))
    first = await geonames.refresh(settings, now=MONDAY)
    assert first["status"] == "refreshed" and first["cities"] == 8
    assert (tmp_path / "geonames" / "cities15000.zip").exists()
    assert not list((tmp_path / "geonames").glob("*.part"))
    assert (await geonames.refresh(settings, now=MONDAY + timedelta(days=29)))["status"] == "fresh"
    assert route.call_count == 1
    again = await geonames.refresh(settings, now=MONDAY + timedelta(days=31))
    assert again["status"] == "refreshed" and route.call_count == 2
    async with session_scope() as s:
        countries = await geonames.rotation_countries(s, settings)
        assert countries == ["DE", "FR", "GB", "LI"]  # PK and IN are excluded markets
        top = await geonames.top_cities(s, ["DE", "LI"], 2)
        assert [c.name for c in top] == ["Berlin", "Hamburg", "Vaduz"]
    # A failed download keeps the cities already loaded.
    respx.get(GEO_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(geonames.GeoNamesError):
        await geonames.refresh(settings, force=True)
    async with session_scope() as s:
        assert len(await geonames.top_cities(s, ["DE"], 10)) == 3


async def test_plan_day_follows_the_rotation_and_never_plans_excluded_markets(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    await _load_geo(tmp_path)
    planned: dict[int, set[str]] = {}
    async with session_scope() as s:
        for d in range(7):
            day = MONDAY.date() + timedelta(days=d)
            await osm_sweep.plan_day(s, settings, day)
            assert await osm_sweep.plan_day(s, settings, day) == 0  # idempotent
        for row in (await s.execute(select(SweepCity))).scalars():
            planned.setdefault(row.day.weekday(), set()).add(row.country)
        cities = [r.city for r in (await s.execute(select(SweepCity))).scalars()]
    assert planned == {0: {"DE"}, 1: {"FR"}, 2: {"GB"}, 3: {"LI"}}
    assert sorted(cities) == ["Berlin", "Hamburg", "London", "Paris", "Vaduz"]  # top 2 per country


@respx.mock
async def test_sweep_stores_merges_creates_leads_and_retries_failed_cities(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    adapters = build_adapters(settings)
    await _load_geo(tmp_path)
    async with session_scope() as s:
        # Known from a registry already: the OSM element merges into it (same phone).
        s.add(Business(name="Café Uno", normalized_name=normalize_name("Café Uno"), country="DE",
                       registry_source="companies_house", registry_number="HRB1",
                       phone="+49301234567"))  # fmt: skip
        # Another OSM place with the same name elsewhere in the country.
        s.add(Business(name="Pizza Roma", normalized_name=normalize_name("Pizza Roma"),
                       country="DE", osm_type="node", osm_id=50, registry_source="osm",
                       registry_number="node/50", phone="+4989111111"))  # fmt: skip
    gateway = 502
    respx.post(MAIN).mock(side_effect=_city_router({"52.52437": BERLIN, "53.57532": gateway}))
    respx.post(MIRROR).mock(side_effect=_city_router({"52.52437": BERLIN, "53.57532": gateway}))

    clock = [MONDAY]
    first = await osm_sweep.run_batch(
        settings, adapters, overpass=_overpass(settings), clock=lambda: clock[0], sleep=_no_sleep
    )
    assert (first["planned"], first["done"], first["retry"], first["failed"]) == (2, 1, 1, 0)
    assert len(first["lead_ids"]) == 2

    async with session_scope() as s:
        rows = {r.city: r for r in (await s.execute(select(SweepCity))).scalars()}
        berlin, hamburg = rows["Berlin"], rows["Hamburg"]
        assert berlin.status == "done" and berlin.attempts == 1 and berlin.mirror == MAIN
        assert berlin.counts == {"elements": 6, "found": 4, "excluded": 1, "created": 2,
                                 "matched": 1, "no_website": 2, "with_website": 1, "leads": 2,
                                 "errors": 0}  # fmt: skip
        assert hamburg.status == "pending" and hamburg.attempts == 1
        assert hamburg.error and "Overpass unavailable" in hamburg.error and "502" in hamburg.error
        assert hamburg.next_attempt_at and hamburg.next_attempt_at >= MONDAY + timedelta(minutes=20)

        biz = {b.name: b for b in (await s.execute(select(Business))).scalars()}
        assert "Grenzladen" not in biz and "Chain Mart" not in biz and "Silent Carpentry" not in biz
        uno = biz["Café Uno"]
        assert (uno.registry_source, uno.osm_type, uno.osm_id) == ("companies_house", "node", 1)
        assert uno.first_seen == MONDAY and uno.last_seen == MONDAY
        assert uno.website_status == WebsiteStatus.OSM_WEBSITE_MISSING and uno.has_website is False
        dental = biz["Dental Zwei"]
        assert dental.domain == "dentalzwei.de" and dental.website_status == "WEBSITE_RECORDED"
        assert dental.industry == "dentist" and (dental.osm_type, dental.osm_id) == ("node", 2)
        contact = (await s.execute(select(Contact))).scalar_one()
        assert (contact.value, contact.verification_status) == (
            "info@baeckerei-drei.de",
            "unverified",
        )
        assert contact.source_url == "https://www.openstreetmap.org/way/3"

        leads = list((await s.execute(select(Lead).order_by(Lead.title))).scalars())
        assert [lead.title for lead in leads] == [
            "Bäckerei Drei — no website on OpenStreetMap",
            "Café Uno — no website on OpenStreetMap",
        ]
        assert {lead.trigger_type for lead in leads} == {TriggerType.NO_WEBSITE}
        assert {lead.source for lead in leads} == {"osm_sweep"} and leads[1].business_id == uno.id
        assert leads[1].source_item_id == "osm:node/1" and leads[1].lane == "fast"
        ev = (await s.execute(select(Evidence).where(Evidence.lead_id == leads[1].id))).scalar_one()
        assert ev.kind == "registry_record" and ev.url == "https://www.openstreetmap.org/node/1"
        assert "OpenStreetMap" in ev.data["attribution"] and "ODbL" in ev.data["license"]
        assert ev.data["city_attribution"] == "GeoNames (geonames.org), CC BY 4.0"

    # Nothing is due yet: the failed city waits for its retry time.
    again = await osm_sweep.run_batch(
        settings, adapters, overpass=_overpass(settings), clock=lambda: clock[0], sleep=_no_sleep
    )
    assert again["done"] == again["retry"] == 0

    # Later that day the mirror answers: the city is retried and done.
    respx.post(MAIN).mock(side_effect=_city_router({"53.57532": HAMBURG}))
    clock[0] = MONDAY + timedelta(hours=3)
    third = await osm_sweep.run_batch(
        settings, adapters, overpass=_overpass(settings), clock=lambda: clock[0], sleep=_no_sleep
    )
    assert third["done"] == 1 and len(third["lead_ids"]) == 1  # Pizza Roma; Café Uno has one
    async with session_scope() as s:
        hamburg = (
            await s.execute(select(SweepCity).where(SweepCity.city == "Hamburg"))
        ).scalar_one()
        assert hamburg.status == "done" and hamburg.attempts == 2 and hamburg.error is None
        uno = (await s.execute(select(Business).where(Business.osm_id == 1))).scalar_one()
        assert uno.first_seen == MONDAY and uno.last_seen == MONDAY + timedelta(hours=3)
        romas = (
            (await s.execute(select(Business).where(Business.name == "Pizza Roma"))).scalars().all()
        )
        assert sorted(b.osm_id or 0 for b in romas) == [50, 51]  # different places, not merged
        assert len((await s.execute(select(Lead))).scalars().all()) == 3

    # The swept business with a website is checked by the site-health trigger, whatever the
    # active industry.
    async with session_scope() as s:
        fetcher = SafeFetcher(
            "TestBot/1.0",
            transport=httpx.MockTransport(lambda r: httpx.Response(
                404 if r.url.path == "/robots.txt" else 500, text="error")),
            min_interval_s=0,
        )  # fmt: skip
        adapters.fetcher = fetcher
        ctx = SourceContext(session=s, adapters=adapters, settings=settings,
                            runtime=await runtime_settings.load(s),
                            config={"per_run": 20, "recheck_days": 30})  # fmt: skip
        found = await SiteHealthSource().fetch(ctx)
        assert [lead.title for lead in found] == ["Dental Zwei — website broken"]


async def _seed_rows(day: date, *rows: tuple[str, str, str]) -> None:
    async with session_scope() as s:
        for i, (country, city, status) in enumerate(rows):
            s.add(SweepCity(day=day, country=country, geonameid=1000 + i, city=city, lat=1.0,
                            lon=2.0, population=100_000 - i, status=status))  # fmt: skip


@respx.mock
async def test_failed_after_max_attempts_and_backlog_after_today(tmp_path: Path) -> None:
    settings = _settings(tmp_path, osm_sweep_max_attempts=2)
    adapters = build_adapters(settings)
    yesterday = MONDAY.date() - timedelta(days=1)
    await _seed_rows(yesterday, ("GB", "Old Pending", "pending"), ("GB", "Old Done", "done"))
    await _seed_rows(MONDAY.date() - timedelta(days=9), ("GB", "Too Old", "pending"))
    order: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        order.append(parse_qs(request.content.decode())["data"][0])
        return httpx.Response(504)

    respx.post(MAIN).mock(side_effect=down)
    respx.post(MIRROR).mock(return_value=httpx.Response(504))
    async with session_scope() as s:
        s.add(SweepCity(day=MONDAY.date(), country="DE", geonameid=1, city="Today", lat=52.5,
                        lon=13.4, population=10))  # fmt: skip
    clock = [MONDAY]
    out = await osm_sweep.run_batch(
        settings, adapters, overpass=_overpass(settings), clock=lambda: clock[0], sleep=_no_sleep
    )
    assert out["retry"] == 2 and out["failed"] == 0
    assert "52.50000" in order[0]  # today's city before yesterday's
    clock[0] = MONDAY + timedelta(hours=7)
    out = await osm_sweep.run_batch(
        settings, adapters, overpass=_overpass(settings), clock=lambda: clock[0], sleep=_no_sleep
    )
    assert out["failed"] == 2
    async with session_scope() as s:
        st = {
            r.city: (r.status, r.attempts) for r in (await s.execute(select(SweepCity))).scalars()
        }
    assert st == {"Today": ("failed", 2), "Old Pending": ("failed", 2), "Old Done": ("done", 0),
                  "Too Old": ("pending", 0)}  # fmt: skip


@respx.mock
async def test_daily_lead_cap_prefers_businesses_with_an_email(tmp_path: Path) -> None:
    settings = _settings(tmp_path, osm_sweep_daily_lead_cap=1)
    await _seed_rows(MONDAY.date(), ("DE", "Berlin", "pending"))
    respx.post(MAIN).mock(return_value=httpx.Response(200, json=BERLIN))
    out = await osm_sweep.run_batch(
        settings, build_adapters(settings), overpass=_overpass(settings), clock=lambda: MONDAY,
        sleep=_no_sleep,
    )  # fmt: skip
    assert out["done"] == 1 and len(out["lead_ids"]) == 1
    async with session_scope() as s:
        lead = (await s.execute(select(Lead))).scalar_one()
        assert lead.title == "Bäckerei Drei — no website on OpenStreetMap"
        assert len((await s.execute(select(Business))).scalars().all()) == 3  # all still stored


async def test_disabled_sweep_does_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path, osm_sweep_enabled=False)
    adapters: Adapters = build_adapters(settings)
    assert (await osm_sweep.run_job(settings, adapters))["status"] == "disabled"
    assert (await osm_sweep.run_batch(settings, adapters))["status"] == "disabled"


async def test_api_sweep_status(tmp_path: Path) -> None:
    from app.config import get_settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    settings = _settings(tmp_path)
    await _load_geo(tmp_path)
    today = datetime.now(UTC).date()
    await _seed_rows(today, ("DE", "Berlin", "done"), ("DE", "Hamburg", "failed"),
                     ("FR", "Paris", "pending"))  # fmt: skip
    async with session_scope() as s:
        s.add(Business(name="Café Uno", normalized_name="cafe uno", country="DE", osm_type="node",
                       osm_id=1, first_seen=datetime.now(UTC), last_seen=datetime.now(UTC)))  # fmt: skip
        s.add(Business(name="Old", normalized_name="old", country="DE", osm_type="node", osm_id=2,
                       last_seen=datetime.now(UTC) - timedelta(days=8)))  # fmt: skip
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        body = (await c.get("/api/discovery/sweep")).json()
    assert body["enabled"] is True and body["day"] == today.isoformat()
    assert body["today_countries"] == geonames.countries_for_day(["DE", "FR", "GB", "LI"], today)
    assert body["totals"] == {"pending": 1, "running": 0, "done": 1, "failed": 1}
    assert [c["city"] for c in body["cities"]] == ["Berlin", "Hamburg", "Paris"]
    week = {w["country"]: w for w in body["week"]}
    assert set(week) == {"DE", "FR", "GB", "LI"}
    assert (week["DE"]["cities"], week["DE"]["done"], week["DE"]["businesses"]) == (2, 1, 1)
    assert week["FR"]["businesses"] is None  # nothing done yet: unknown
    assert week["GB"]["cities"] is None and week["GB"]["businesses"] is None
    assert body["geonames"]["cities"] == 8 and body["geonames"]["countries"] == 4
    assert body["attribution"] == {
        "geonames": "GeoNames (geonames.org), CC BY 4.0",
        "osm": "© OpenStreetMap contributors (ODbL)",
    }
    assert body["settings"]["radius_m"] == 8000


def _el(osm_id: int, name: str, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": osm_id, "lat": 41.0, "lon": 28.9,
            "tags": {"name": name, **tags}}  # fmt: skip


async def test_name_only_match_keeps_its_own_website_and_city_is_not_lost() -> None:
    """Live sweep bug: a new element matched another OSM element by name only; its website was
    merged onto that element before a second record was created with the same domain, and the
    unique domain failed the whole city."""
    settings = Settings(_env_file=None, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    first = Overpass(["https://x.test"]).parse_sweep(
        {"elements": [_el(10, "Pizza Roma", amenity="restaurant", phone="+39 06 1111111")]},
        "IT",
        "Rome",
    )
    second = Overpass(["https://x.test"]).parse_sweep(
        {"elements": [
            _el(11, "Pizza Roma", amenity="restaurant", phone="+39 06 2222222", website="https://pizzaroma2.it/"),
            _el(12, "Mission of Hungary", office="diplomatic", phone="+1 212 0000000", website="https://gov.hu"),
        ]}, "IT", "Rome",
    )  # fmt: skip
    assert [f.name for f in second] == ["Pizza Roma"]  # diplomatic offices are not prospects
    async with session_scope() as s:
        a = await osm_sweep.store_one(s, settings, first[0], industry="restaurant")
        b = await osm_sweep.store_one(s, settings, second[0], industry="restaurant")
    assert a is not None and b is not None and a[0].id != b[0].id
    async with session_scope() as s:
        rows = {r.osm_id: r for r in (await s.execute(select(Business))).scalars()}
    assert rows[10].domain is None  # the other place did not get this website
    assert rows[11].domain == "pizzaroma2.it"
