"""Business search (no-website discovery) with recorded response shapes: Overpass, France's
recherche-entreprises, Norway's brreg, Companies House; country routing, excluded markets at
job start and before insert, dedup across sources, OSM attribution and the API. No live calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.adapters.opendata import (
    CompaniesHouseSearch,
    FrRechercheEntreprises,
    NoBrreg,
    Overpass,
    WebsiteStatus,
    route_for,
)
from app.config import Settings
from app.db.models import Business, Contact, DiscoveryJob
from app.db.session import session_scope
from app.services import business_search
from app.services.business_search import ExcludedMarketError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

OVERPASS_A = "https://overpass-a.test/api/interpreter"
OVERPASS_B = "https://overpass-b.test/api/interpreter"


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "overpass_api_url": OVERPASS_A, "overpass_fallback_urls": OVERPASS_B,
        "overpass_max_requests_per_minute": 1000, "companies_house_api_key": "ch",
    }  # fmt: skip
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **{**base, **kw})  # type: ignore[call-arg]


OSM = {"version": 0.6, "elements": [
    {"type": "node", "id": 101, "lat": 53.8, "lon": -1.55, "tags": {
        "name": "Leeds Pipes", "craft": "plumber", "phone": "+44 113 496 0000",
        "addr:street": "Kirkgate", "addr:housenumber": "12", "addr:city": "Leeds",
        "addr:postcode": "LS1 6BR", "email": "Info@LeedsPipes.example"}},
    {"type": "way", "id": 202, "center": {"lat": 53.79, "lon": -1.54}, "tags": {
        "name": "Aire Plumbing", "craft": "plumber", "website": "https://www.aireplumbing.co.uk/"}},
    {"type": "node", "id": 303, "lat": 1, "lon": 1, "tags": {"craft": "plumber"}},
    {"type": "node", "id": 404, "lat": 31.5, "lon": 74.3, "tags": {
        "name": "Border Plumbing", "craft": "plumber", "addr:country": "PK"}},
]}  # fmt: skip
FR = {"results": [{"siren": "950752345", "nom_complet": "LE PLOMBIER DE LYON",
                   "activite_principale": "43.22B", "date_creation": "2023-04-01",
                   "siege": {"adresse": "11 B AVENUE FRANKLIN ROOSEVELT", "code_postal": "69150",
                             "libelle_commune": "DECINES-CHARPIEU", "latitude": "45.76",
                             "longitude": "4.93"}}],
      "total_results": 1, "page": 1, "per_page": 25, "total_pages": 1}  # fmt: skip
NO = {"_embedded": {"enheter": [
    {"organisasjonsnummer": "982047382", "navn": "ROR & RORLEGGERE DA", "hjemmeside": "navn.no",
     "epostadresse": "firmapost@navn.no", "telefon": "95 06 59 09",
     "naeringskode1": {"kode": "43.220"}, "forretningsadresse": {
         "adresse": ["Skogveien 61B"], "postnummer": "1368", "poststed": "STABEKK", "landkode": "NO"}},
    {"organisasjonsnummer": "900000001", "navn": "Bergen VVS AS",
     "forretningsadresse": {"adresse": ["Strandgaten 1"], "postnummer": "5013",
                            "poststed": "BERGEN", "landkode": "NO"}}]},
      "page": {"number": 0, "size": 50, "totalElements": 2, "totalPages": 1}}  # fmt: skip
CH = {"items": [{"company_number": "12345678", "company_name": "LEEDS PIPES LTD",
                 "sic_codes": ["43220"], "date_of_creation": "2024-01-02",
                 "registered_office_address": {"address_line_1": "12 Kirkgate",
                                               "locality": "Leeds", "postal_code": "LS1 6BR"}}]}  # fmt: skip


def test_country_routing() -> None:
    assert route_for("gb") == ("osm", "companies_house")
    assert route_for("FR") == ("osm", "fr_recherche") and route_for("NO") == ("osm", "no_brreg")
    assert route_for("BR") == ("osm",)
    assert business_search.available_sources(_settings(companies_house_api_key=None), "GB") == [
        "osm"
    ]


def test_parsers_and_website_status() -> None:
    osm = Overpass([OVERPASS_A], attribution="© OpenStreetMap contributors (ODbL)").parse(
        OSM, "GB", "plumber"
    )
    assert [b.name for b in osm] == [
        "Leeds Pipes",
        "Aire Plumbing",
        "Border Plumbing",
    ]  # unnamed dropped
    pipes, aire, border = osm
    assert pipes.website_status == WebsiteStatus.OSM_WEBSITE_MISSING
    assert pipes.address == "12 Kirkgate, LS1 6BR, Leeds" and pipes.source_id == "node/101"
    assert aire.website_status == WebsiteStatus.WEBSITE_RECORDED and aire.lat == 53.79
    assert border.country == "PK" and pipes.attribution and "OpenStreetMap" in pipes.attribution
    fr = FrRechercheEntreprises.parse(FR, "plumber")[0]
    assert (fr.source_id, fr.country, fr.website_status) == (
        "950752345",
        "FR",
        WebsiteStatus.WEBSITE_UNCONFIRMED,
    )
    assert fr.url == "https://annuaire-entreprises.data.gouv.fr/entreprise/950752345"
    no = NoBrreg.parse(NO, "plumber")
    assert (
        no[0].website_status == WebsiteStatus.WEBSITE_RECORDED
        and no[0].email == "firmapost@navn.no"
    )
    assert no[1].website_status == WebsiteStatus.WEBSITE_UNCONFIRMED and no[1].city == "BERGEN"
    ch = CompaniesHouseSearch.parse(CH, "plumber")[0]
    assert ch.address == "12 Kirkgate, Leeds, LS1 6BR" and ch.country == "GB"
    q = Overpass.query("GB", 'Le"eds', "locksmith", 20)
    assert 'area["ISO3166-1"="GB"]' in q and 'area["name"="Leeds"]' in q
    assert 'nwr["shop"="locksmith"]["name"](area.a);' in q and "out center tags 20;" in q


async def test_job_start_rejects_excluded_markets_and_bad_input() -> None:
    settings = _settings()
    async with session_scope() as s:
        for code in ("PK", "in", "BD"):
            with pytest.raises(ExcludedMarketError):
                await business_search.start_job(
                    s, settings, country=code, city="X", category="plumber"
                )
        with pytest.raises(ValueError, match="unknown category"):
            await business_search.start_job(
                s, settings, country="GB", city="Leeds", category="spaceship"
            )
        job = await business_search.start_job(
            s, settings, country="gb", city="Leeds", category="plumber"
        )
        assert (job.country, job.sources, job.status) == (
            "GB",
            ["osm", "companies_house"],
            "queued",
        )
        assert (await s.execute(select(DiscoveryJob))).scalars().all() == [job]


@respx.mock
async def test_run_job_overpass_fallback_dedup_and_excluded_before_insert() -> None:
    settings = _settings()
    a = respx.post(OVERPASS_A).mock(return_value=httpx.Response(502))
    b = respx.post(OVERPASS_B).mock(return_value=httpx.Response(200, json=OSM))
    ch = respx.get(CompaniesHouseSearch.ENDPOINT).mock(return_value=httpx.Response(200, json=CH))
    async with session_scope() as s:
        job = await business_search.start_job(
            s, settings, country="GB", city="Leeds", category="plumber"
        )
        job_id = job.id
    await business_search.run_job(job_id, settings, industry="home_services")
    assert a.called and b.called
    assert ch.calls[0].request.url.params["sic_codes"] == "43220"
    async with session_scope() as s:
        job = await s.get(DiscoveryJob, job_id)
        assert job is not None and job.status == "done" and job.errors == []
        assert job.counts["excluded"] == 1 and job.counts["by_source"] == {
            "osm": 3,
            "companies_house": 1,
        }
        assert job.attribution and "OpenStreetMap" in job.attribution
        names = {b.name: b for b in (await s.execute(select(Business))).scalars()}
        assert "Border Plumbing" not in names  # PK result never stored
        pipes = names["Leeds Pipes"]
        assert (
            pipes.website_status == WebsiteStatus.OSM_WEBSITE_MISSING and pipes.has_website is False
        )
        assert "LEEDS PIPES LTD" not in names  # matched to the OSM record (same business)
        assert names["Aire Plumbing"].domain == "aireplumbing.co.uk"
        contact = (await s.execute(select(Contact))).scalar_one()
        assert (
            contact.value == "info@leedspipes.example"
            and contact.verification_status == "unverified"
        )
        assert contact.source_url == "https://www.openstreetmap.org/node/101"
        results = await business_search.job_results(s, job)
    assert {r["name"] for r in results} == {"Leeds Pipes", "Aire Plumbing"}
    assert all(r["is_osm"] for r in results)


@respx.mock
async def test_one_failing_source_keeps_the_others() -> None:
    settings = _settings()
    respx.post(OVERPASS_A).mock(return_value=httpx.Response(504))
    respx.post(OVERPASS_B).mock(side_effect=httpx.ReadTimeout("slow"))
    respx.get("https://recherche-entreprises.api.gouv.fr/search").mock(
        return_value=httpx.Response(200, json=FR)
    )
    async with session_scope() as s:
        job = await business_search.start_job(
            s, settings, country="FR", city="Lyon", category="plumber"
        )
        job_id = job.id
    await business_search.run_job(job_id, settings)
    async with session_scope() as s:
        job = await s.get(DiscoveryJob, job_id)
        assert job is not None and job.status == "done"
        assert job.errors and job.errors[0].startswith("osm: Overpass unavailable")
        assert job.counts["by_source"] == {"fr_recherche": 1} and job.attribution is None


@respx.mock
async def test_api_search_jobs_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings  # noqa: PLC0415
    from app.main import create_app  # noqa: PLC0415

    settings = _settings()
    brreg = respx.get("https://data.brreg.no/enhetsregisteret/api/enheter").mock(
        return_value=httpx.Response(200, json=NO)
    )
    respx.post(OVERPASS_A).mock(return_value=httpx.Response(200, json={"elements": []}))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        opts = (await c.get("/api/discovery/options")).json()
        assert opts["excluded_countries"] == ["BD", "IN", "PK"] and "plumber" in opts["categories"]
        refused = await c.post(
            "/api/discovery/search", json={"country": "PK", "city": "Lahore", "category": "plumber"}
        )
        assert refused.status_code == 422 and "excluded market" in refused.json()["detail"]
        started = await c.post(
            "/api/discovery/search", json={"country": "NO", "city": "Bergen", "category": "plumber"}
        )
        assert started.status_code == 202
        job_id = started.json()["id"]
        jobs = (await c.get("/api/discovery/jobs")).json()
        assert [j["id"] for j in jobs] == [job_id]
        detail = (await c.get(f"/api/discovery/jobs/{job_id}")).json()
        assert detail["status"] == "done" and len(detail["results"]) == 2
        assert {r["website_status"] for r in detail["results"]} == {
            "WEBSITE_RECORDED",
            "WEBSITE_UNCONFIRMED",
        }
        assert (
            await c.get("/api/discovery/jobs/00000000-0000-0000-0000-000000000000")
        ).status_code == 404
    params = brreg.calls[0].request.url.params
    assert params["naeringskode"] == "43.22" and params["forretningsadresse.poststed"] == "BERGEN"
