"""OpenStreetMap weekly sweep, pure parts: GeoNames parsing, the weekday rotation, the Overpass
sweep query and parser, mirror fallback with backoff (respx, sleeps recorded instead of
waited) and the related settings. No live calls."""

from __future__ import annotations

import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.adapters.opendata import (
    SWEEP_AMENITIES,
    OpenDataError,
    Overpass,
    WebsiteStatus,
    bot_user_agent,
    osm_ref,
    sweep_category,
)
from app.config import DEFAULT_OVERPASS_MIRRORS, Settings
from app.services import geonames
from app.services.osm_sweep import industry_for, retry_delay

MAIN = "https://overpass-api.de/api/interpreter"
KUMI = "https://overpass.kumi.systems/api/interpreter"
COFFEE = "https://overpass.private.coffee/api/interpreter"


def geo_line(
    gid: int, name: str, cc: str, pop: int, code: str = "PPL", lat: float = 1.0,
    lon: float = 2.0, cls: str = "P",
) -> str:  # fmt: skip
    """One line in the GeoNames dump format (19 tab-separated columns)."""
    cols = [str(gid), name, name, "Alt1,Alt2", str(lat), str(lon), cls, code, cc, "", "01",
            "", "", "", str(pop), "", "100", "Europe/Berlin", "2026-01-01"]  # fmt: skip
    return "\t".join(cols) + "\n"


GEO_FIXTURE = "".join([
    geo_line(2950159, "Berlin", "DE", 3_426_354, "PPLC", 52.52437, 13.41053),
    geo_line(2911298, "Hamburg", "DE", 1_739_117, "PPLA", 53.57532, 10.01534),
    geo_line(6545310, "Mitte", "DE", 300_000, "PPLX"),  # a district, not a city
    geo_line(1, "Old Town", "DE", 50_000, "PPLH"),  # historical place
    geo_line(2, "Lake", "DE", 50_000, "LK", cls="H"),  # not a populated place
    "broken line without tabs\n",
    geo_line(3, "No Number", "DE", 0),  # population 0
    geo_line(4, "Bad Country", "D1", 90_000),
    geo_line(3042030, "Vaduz", "LI", 15_000, "PPLC", 47.14151, 9.52154),
    geo_line(1172451, "Lahore", "PK", 6_310_888, "PPLA"),
])  # fmt: skip


def test_geonames_parse_lines_skips_sections_and_malformed_rows() -> None:
    rows = list(geonames.parse_lines(GEO_FIXTURE.splitlines(keepends=True)))
    assert [r.name for r in rows] == ["Berlin", "Hamburg", "Vaduz", "Lahore"]
    berlin = rows[0]
    assert (berlin.geonameid, berlin.country, berlin.population, berlin.feature_code) == (
        2950159,
        "DE",
        3_426_354,
        "PPLC",
    )
    assert (berlin.lat, berlin.lon, berlin.timezone) == (52.52437, 13.41053, "Europe/Berlin")
    # Windows line endings are tolerated.
    crlf = geo_line(5, "Crlf", "FR", 20_000).replace("\n", "\r\n")
    assert [r.name for r in geonames.parse_lines([crlf])] == ["Crlf"]


def test_geonames_iter_zip_streams_the_txt_member(tmp_path: Path) -> None:
    path = tmp_path / "cities15000.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.md", "not data")
        zf.writestr("cities15000.txt", GEO_FIXTURE)
    assert [r.country for r in geonames.iter_zip(path)] == ["DE", "DE", "LI", "PK"]
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("readme.md", "nothing")
    with pytest.raises(geonames.GeoNamesError):
        list(geonames.iter_zip(empty))


def test_rotation_covers_every_country_once_a_week() -> None:
    countries = [
        f"{a}{b}" for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ][:190]
    monday = date(2026, 11, 16)
    assert monday.weekday() == 0
    days = [
        geonames.countries_for_day(reversed(countries), monday + timedelta(d)) for d in range(7)
    ]
    flat = [c for day in days for c in day]
    assert sorted(flat) == sorted(countries) and len(flat) == len(set(flat))
    ordered = sorted(countries)
    assert days[0][:2] == [ordered[0], ordered[7]]  # index % 7 == weekday
    assert days[3][0] == ordered[3] and days[6][0] == ordered[6]
    assert {len(d) for d in days} <= {27, 28}  # ~190 markets -> ~27 countries a day
    # The same weekday next week brings the same countries.
    assert geonames.countries_for_day(countries, monday + timedelta(7)) == days[0]
    assert geonames.weekday_of(countries, ordered[10]) == 3
    assert geonames.weekday_of(countries, "ZZ") is None


def test_sweep_query_radius_tag_groups_and_contact_filter() -> None:
    q = Overpass.sweep_query(47.14151, 9.52154, 8000)
    assert q.startswith("[out:json][timeout:90];(") and q.endswith(");out center tags;")
    around = "(around:8000,47.14151,9.52154);"
    assert q.count(around) == 4
    contact = '[~"^(phone|contact:phone|mobile|contact:mobile|email|contact:email)$"~"."]'
    for key in ("shop", "office", "craft"):
        assert f'nwr["{key}"]["name"]{contact}{around}' in q
    amenity = '["amenity"~"^(' + "|".join(SWEEP_AMENITIES) + ')$"]'
    assert f'nwr{amenity}["name"]{contact}{around}' in q
    assert set(SWEEP_AMENITIES) == {
        "restaurant", "cafe", "bar", "fast_food", "dentist", "clinic", "doctors", "pharmacy",
        "beauty", "veterinary", "bank",
    }  # fmt: skip
    assert "(around:12000," in Overpass.sweep_query(1, 2, 12000)


SWEEP_BODY: dict[str, Any] = {"elements": [
    {"type": "node", "id": 1, "lat": 47.1, "lon": 9.5, "tags": {
        "name": "Café Uno", "amenity": "cafe", "phone": "+423 230 00 00; +423 230 00 01"}},
    {"type": "node", "id": 2, "lat": 47.1, "lon": 9.5, "tags": {
        "name": "Dental Zwei", "amenity": "dentist", "email": "praxis@dentalzwei.li",
        "website": "https://www.dentalzwei.li/"}},
    {"type": "way", "id": 3, "center": {"lat": 47.2, "lon": 9.6}, "tags": {
        "name": "Bäckerei Drei", "shop": "bakery", "contact:email": "Info@Baeckerei-Drei.li"}},
    {"type": "node", "id": 4, "tags": {"name": "Chain Mart", "shop": "supermarket",
                                       "brand:wikidata": "Q1", "phone": "+423 1"}},
    {"type": "node", "id": 5, "tags": {"name": "Silent Carpentry", "craft": "carpenter"}},
    {"type": "node", "id": 6, "tags": {"name": "Law Office", "office": "lawyer",
                                       "phone": "+423 2", "url": "http://law.li"}},
    {"type": "node", "id": 7, "tags": {"name": "Kanzlei", "office": "notary", "mobile": "+41 3",
                                       "contact:website": "https://kanzlei.ch",
                                       "addr:country": "ch"}},
    {"type": "node", "id": 8, "tags": {"shop": "kiosk", "phone": "+423 4"}},
    {"type": "relation", "id": 9, "tags": {"name": "Odd Bank", "amenity": "bank",
                                           "phone": "+423 5"}},
]}  # fmt: skip


def test_parse_sweep_keeps_reachable_named_businesses_and_splits_by_website() -> None:
    found = Overpass(["x"], per_minute=1000).parse_sweep(SWEEP_BODY, "LI", "Vaduz")
    by = {f.name: f for f in found}
    assert set(by) == {
        "Café Uno",
        "Dental Zwei",
        "Bäckerei Drei",
        "Law Office",
        "Kanzlei",
        "Odd Bank",
    }
    assert by["Café Uno"].website_status == WebsiteStatus.OSM_WEBSITE_MISSING
    assert by["Café Uno"].phone == "+423 230 00 00"  # first of several values
    assert by["Café Uno"].city == "Vaduz" and by["Café Uno"].category == "amenity=cafe"
    assert by["Bäckerei Drei"].email == "Info@Baeckerei-Drei.li"
    assert by["Bäckerei Drei"].lat == 47.2 and by["Bäckerei Drei"].category == "shop=bakery"
    # website, url and contact:website all count as a recorded website
    for name in ("Dental Zwei", "Law Office", "Kanzlei"):
        assert by[name].website_status == WebsiteStatus.WEBSITE_RECORDED, name
    assert by["Kanzlei"].country == "CH" and by["Law Office"].country == "LI"
    assert by["Odd Bank"].source_id == "relation/9"
    assert osm_ref(by["Bäckerei Drei"]) == ("way", 3)
    assert all("OpenStreetMap" in (f.attribution or "") for f in found)
    assert sweep_category({"amenity": "parking", "shop": "car"}) == "shop=car"
    assert sweep_category({"amenity": "parking"}) is None
    assert industry_for("amenity=restaurant") == "restaurant" and industry_for("shop=yes") == "shop"


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


@respx.mock
async def test_mirrors_in_order_then_backoff_with_jitter() -> None:
    order: list[str] = []

    def answer(name: str, responses: list[Any]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            order.append(name)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        return handler

    respx.post(MAIN).mock(
        side_effect=answer(
            "main", [httpx.Response(502, text="at capacity"), httpx.Response(200, json=SWEEP_BODY)]
        )
    )
    respx.post(KUMI).mock(
        side_effect=answer(
            "kumi", [httpx.Response(504), httpx.Response(429, headers={"Retry-After": "45"})]
        )
    )
    respx.post(COFFEE).mock(side_effect=answer("coffee", [
        httpx.ReadTimeout("slow"), httpx.Response(200, json={"remark": "x"})]))  # fmt: skip
    sleeps = _Sleeps()
    op = Overpass([MAIN, KUMI, COFFEE], per_minute=1000, rounds=3, backoff_s=10, sleep=sleeps)
    found, mirror, elements = await op.sweep(47.1, 9.5, 8000, country="LI", city="Vaduz")
    assert order == ["main", "kumi", "coffee", "main"] and mirror == MAIN
    assert elements == len(SWEEP_BODY["elements"]) and len(found) == 6
    assert len(sleeps.calls) == 1 and 10 <= sleeps.calls[0] <= 20  # base 10 s + jitter

    # A 429 with Retry-After stretches the wait before the next pass.
    order.clear()
    respx.post(MAIN).mock(
        side_effect=answer(
            "main", [httpx.Response(503), httpx.Response(200, json={"elements": []})]
        )
    )
    respx.post(KUMI).mock(
        side_effect=answer("kumi", [httpx.Response(429, headers={"Retry-After": "45"})])
    )
    respx.post(COFFEE).mock(side_effect=answer("coffee", [httpx.ConnectTimeout("down")]))
    sleeps.calls.clear()
    assert (await op.run("q"))[1] == MAIN
    assert len(sleeps.calls) == 1 and 45 <= sleeps.calls[0] <= 55


@respx.mock
async def test_mirror_failures_runtime_remarks_and_bad_queries() -> None:
    sleeps = _Sleeps()
    respx.post(MAIN).mock(return_value=httpx.Response(
        200, json={"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 1 after 91 seconds."}))  # fmt: skip
    respx.post(KUMI).mock(return_value=httpx.Response(504))
    respx.post(COFFEE).mock(side_effect=httpx.ReadTimeout("slow"))
    op = Overpass([MAIN, KUMI, COFFEE], per_minute=1000, rounds=3, backoff_s=10, sleep=sleeps)
    with pytest.raises(OpenDataError, match="Overpass unavailable"):
        await op.run("q")
    assert len(sleeps.calls) == 2  # between the three passes
    assert 10 <= sleeps.calls[0] <= 20 and 20 <= sleeps.calls[1] <= 30  # doubling + jitter
    assert respx.calls.call_count == 9

    respx.post(MAIN).mock(return_value=httpx.Response(400, text="parse error"))
    sleeps.calls.clear()
    with pytest.raises(OpenDataError, match="rejected the query"):
        await op.run("q")
    assert sleeps.calls == []


@respx.mock
async def test_user_agent_carries_the_contact_and_query_is_posted() -> None:
    route = respx.post(MAIN).mock(return_value=httpx.Response(200, json={"elements": []}))
    op = Overpass([MAIN], per_minute=1000, contact="owner@example.com")
    await op.sweep(10.0, 20.0, 8000, country="DE", city="X")
    request = route.calls[0].request
    assert "contact: owner@example.com" in request.headers["user-agent"]
    assert "(around:8000,10.00000,20.00000)" in parse_qs(request.content.decode())["data"][0]
    assert "contact:" not in bot_user_agent("")


def test_overpass_settings_mirrors_and_contact() -> None:
    def s(**kw: Any) -> Settings:
        return Settings(_env_file=None, **kw)  # type: ignore[call-arg]

    assert s().overpass_urls == [MAIN, KUMI, COFFEE] == list(DEFAULT_OVERPASS_MIRRORS)
    assert s(overpass_fallback_urls="none").overpass_urls == [MAIN]
    assert s(overpass_api_url=KUMI, overpass_fallback_urls=f"{MAIN}, {KUMI}").overpass_urls == [
        KUMI,
        MAIN,
    ]
    assert s(gmail_sender="owner@gmail.com").overpass_contact == "owner@gmail.com"
    assert (
        s(gmail_sender="owner@gmail.com", overpass_contact_email="osm@example.com").overpass_contact
        == "osm@example.com"
    )


def test_retry_delay_grows_and_is_capped() -> None:
    first, second, late = retry_delay(1), retry_delay(2), retry_delay(20)
    assert timedelta(minutes=20) <= first <= timedelta(minutes=25)
    assert timedelta(minutes=40) <= second <= timedelta(minutes=45)
    assert timedelta(hours=6) <= late <= timedelta(hours=6, minutes=5)
