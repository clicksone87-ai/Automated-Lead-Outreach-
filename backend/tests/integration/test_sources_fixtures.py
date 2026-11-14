"""Discovery sources against recorded API/page fixtures (no live calls)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import func, select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.adapters.search import BraveSearch
from app.agents.discovery.base import SourceContext, looks_like_request
from app.agents.discovery.sources.reddit import RedditSource, sync_deletions
from app.agents.discovery.sources.registries import CompaniesHouseSource
from app.agents.discovery.sources.rss import RssSource
from app.agents.discovery.sources.search_api import SearchApiSource
from app.agents.discovery.sources.social import XSource
from app.agents.discovery.sources.web_directory import WebDirectorySource
from app.config import Settings
from app.db.models import Business, Cost, Lead, TriggerType
from app.db.session import session_scope
from app.services import runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

UA = "TestBot/1.0"

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><guid>a1</guid><title>Looking for a web designer for my roofing company</title>
<link>https://forum.test/a1</link><description>Need a new website with quotes. Budget $2k.</description>
<pubDate>Fri, 13 Nov 2026 10:00:00 GMT</pubDate></item>
<item><guid>a2</guid><title>Best ladders 2026?</title><link>https://forum.test/a2</link>
<description>Which ladder brand do you recommend?</description></item>
</channel></rss>"""

DIRECTORY = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Plumber","name":"Pipe Pros","telephone":"+44 113 496 0001",
  "address":{"@type":"PostalAddress","addressLocality":"Leeds","postalCode":"LS1 4AP","addressCountry":"GB"}},
 {"@type":"Electrician","name":"Spark Ltd","url":"https://spark-ltd.co.uk",
  "address":{"@type":"PostalAddress","addressLocality":"York","addressCountry":"GB"}},
 {"@type":"Article","name":"Not a business"}
]}</script></head><body>dir</body></html>"""


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, demo_mode=False, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


def _fetcher(routes: dict[str, httpx.Response]) -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return routes.get(str(request.url), httpx.Response(404))

    return SafeFetcher(UA, transport=httpx.MockTransport(handler), min_interval_s=0)


async def _ctx(
    session: Any, settings: Settings, config: dict[str, Any], fetcher: SafeFetcher | None = None
) -> SourceContext:
    adapters = build_adapters(settings)
    if fetcher:
        adapters.fetcher = fetcher
    return SourceContext(
        session=session,
        adapters=adapters,
        settings=settings,
        runtime=await runtime_settings.load(session),
        config=config,
    )


def test_request_prefilter() -> None:
    assert looks_like_request("Looking for someone to build a website for my bakery")
    assert looks_like_request("[Hiring] logo designer for my plumbing company")
    assert not looks_like_request("I just launched my website, check it out")
    assert not looks_like_request("Need a plumber in Leeds")


async def test_rss_source_filters_requests() -> None:
    async with session_scope() as s:
        ctx = await _ctx(
            s,
            _settings(),
            {"feeds": [{"url": "https://forum.test/feed.xml"}]},
            _fetcher({"https://forum.test/feed.xml": httpx.Response(200, content=RSS)}),
        )
        leads = await RssSource().fetch(ctx)
    assert [lead.source_url for lead in leads] == ["https://forum.test/a1"]
    assert leads[0].trigger_type == TriggerType.EXPLICIT_REQUEST
    assert leads[0].posted_at == datetime(2026, 11, 13, 10, tzinfo=UTC)


async def test_web_directory_creates_no_website_leads_and_stores_sites() -> None:
    async with session_scope() as s:
        ctx = await _ctx(
            s,
            _settings(),
            {
                "listing_urls": [{"url": "https://dir.test/plumbers", "country": "GB"}],
                "pages_per_run": 5,
            },
            _fetcher(
                {
                    "https://dir.test/plumbers": httpx.Response(
                        200, text=DIRECTORY, headers={"content-type": "text/html"}
                    )
                }
            ),
        )
        leads = await WebDirectorySource().fetch(ctx)
        assert len(leads) == 1
        assert leads[0].trigger_type == TriggerType.NO_WEBSITE
        assert leads[0].business and leads[0].business.name == "Pipe Pros"
        stored = (
            await s.execute(select(Business).where(Business.domain == "spark-ltd.co.uk"))
        ).scalar_one()
        assert stored.has_website and stored.country == "GB"


@respx.mock
async def test_search_api_source_with_brave_fixture() -> None:
    respx.get(BraveSearch.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Need a new website for my HVAC business",
                            "url": "https://forum.test/t/9",
                            "description": "Looking for a web designer.",
                        },
                    ]
                }
            },
        )
    )
    page = "<html><title>Need a new website</title><body><p>We need a new website for our HVAC company in Denver. Looking for quotes.</p></body></html>"
    async with session_scope() as s:
        settings = _settings(brave_search_api_key=SecretStr("k"))
        ctx = await _ctx(
            s,
            settings,
            {"queries_per_run": 1, "freshness": "pw"},
            _fetcher(
                {
                    "https://forum.test/t/9": httpx.Response(
                        200, text=page, headers={"content-type": "text/html"}
                    )
                }
            ),
        )
        leads = await SearchApiSource().fetch(ctx)
        assert len(leads) == 1
        assert leads[0].source_url == "https://forum.test/t/9"
        assert ctx.state["cursor"] == 1
        charged = (
            await s.execute(select(func.sum(Cost.amount_usd)).where(Cost.provider == "brave"))
        ).scalar_one()
        assert charged == pytest.approx(0.005)
    sent = respx.calls.last.request
    assert sent.headers["X-Subscription-Token"] == "k"
    assert "google" not in str(sent.url)


@respx.mock
async def test_companies_house_fixture() -> None:
    route = respx.get(CompaniesHouseSource.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "company_name": "BRIGHT SPARKS ELECTRICAL LTD",
                        "company_number": "15999999",
                        "date_of_creation": "2026-11-10",
                        "sic_codes": ["43210"],
                        "registered_office_address": {
                            "address_line_1": "1 High St",
                            "locality": "Leeds",
                            "postal_code": "LS1 1AA",
                        },
                    },
                ]
            },
        )
    )
    async with session_scope() as s:
        ctx = await _ctx(
            s,
            _settings(companies_house_api_key=SecretStr("ch-key")),
            {"lookback_days": 7, "max_results": 100},
        )
        leads = await CompaniesHouseSource().fetch(ctx)
    assert leads[0].trigger_type == TriggerType.NEW_BUSINESS
    assert leads[0].business and leads[0].business.registry_number == "15999999"
    assert "43220" in route.calls.last.request.url.params["sic_codes"]
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_reddit_fixture_and_deletion_sync() -> None:
    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "t"})
    )
    respx.get("https://oauth.reddit.com/r/forhire/new").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc",
                                "title": "[Hiring] Need a logo and website for my cleaning company",
                                "selftext": "Budget $800",
                                "permalink": "/r/forhire/comments/abc/x/",
                                "author": "u1",
                                "created_utc": 1_794_000_000,
                            }
                        },
                        {
                            "data": {
                                "id": "def",
                                "title": "[For Hire] I design logos",
                                "selftext": "",
                                "permalink": "/r/forhire/comments/def/",
                                "author": "u2",
                                "created_utc": 1_794_000_000,
                            }
                        },
                    ]
                }
            },
        )
    )
    settings = _settings(
        reddit_client_id="id",
        reddit_client_secret=SecretStr("sec"),
        reddit_user_agent="test-agent/1.0",
    )
    async with session_scope() as s:
        ctx = await _ctx(s, settings, {"subreddits": ["forhire"], "limit": 100})
        leads = await RedditSource().fetch(ctx)
        assert [lead.source_item_id for lead in leads] == ["t3_abc"]
        s.add(
            Lead(
                source="reddit",
                source_url=leads[0].source_url,
                source_item_id="t3_abc",
                trigger_type=TriggerType.EXPLICIT_REQUEST,
                raw_text="Budget $800",
                pipeline_state={},
            )
        )
        await s.flush()
    respx.get("https://oauth.reddit.com/api/info").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "name": "t3_abc",
                                "author": "[deleted]",
                                "selftext": "[deleted]",
                            }
                        },
                    ]
                }
            },
        )
    )
    async with session_scope() as s:
        assert await sync_deletions(s, settings) == 1
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Lead))).scalar_one() == 0


@respx.mock
async def test_x_source_is_budget_charged() -> None:
    respx.get(XSource.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "text": "Looking for a web designer, need a website for my plumbing biz",
                        "created_at": "2026-11-13T09:00:00.000Z",
                    }
                ],
                "meta": {"newest_id": "1"},
            },
        )
    )
    async with session_scope() as s:
        ctx = await _ctx(s, _settings(x_bearer_token=SecretStr("b")), {"max_results": 10})
        leads = await XSource().fetch(ctx)
        assert len(leads) == 1
        assert ctx.state["since_id"] == "1"
        spent = (
            await s.execute(select(func.sum(Cost.amount_usd)).where(Cost.provider == "x"))
        ).scalar_one()
        assert spent == pytest.approx(0.1)
