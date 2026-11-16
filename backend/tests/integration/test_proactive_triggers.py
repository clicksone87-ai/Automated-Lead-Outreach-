"""Proactive triggers: signal detection (pure) and both trigger sources (fixtures, Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.agents.discovery.base import SourceContext
from app.agents.discovery.proactive_triggers.signals import (
    classify_site,
    domain_candidates,
    page_mentions_business,
)
from app.agents.discovery.proactive_triggers.sources import NoWebsiteSource, SiteHealthSource
from app.config import Settings
from app.db.models import Business, TriggerType
from app.db.session import session_scope
from app.services import runtime_settings

NOW = datetime(2026, 11, 16, tzinfo=UTC)
OLD_SITE = """<html><head><meta name="generator" content="Microsoft FrontPage 4.0"></head>
<body><table width="800"><tr><td><table><tr><td>Welcome</td></tr></table></td></tr></table>
<table width="800"><tr><td>Plumbing</td></tr></table><marquee>Call us!</marquee>
<p>&copy; 2015 Old Pipes</p></body></html>"""
NEW_SITE = """<html><head><meta name="viewport" content="width=device-width"></head>
<body><p>&copy; 2026 Fresh Pipes</p></body></html>"""


def test_classify_site_signals() -> None:
    old = classify_site(200, OLD_SITE, "http://oldpipes.com/", now=NOW)
    assert old.status == "outdated"
    assert {
        "no_https",
        "no_mobile_viewport",
        "copyright_2015",
        "table_layout",
        "obsolete_tags",
        "old_generator",
    } <= set(old.signals)
    assert classify_site(200, NEW_SITE, "https://freshpipes.com/", now=NOW).status == "ok"
    assert classify_site(503, "", "https://x.com/").status == "broken"
    assert classify_site(None, None, None).status == "unreachable"


def test_domain_candidates_and_mentions() -> None:
    assert domain_candidates("Bright Sparks Electrical Ltd", "GB") == [
        "brightsparkselectrical.co.uk",
        "bright-sparks-electrical.co.uk",
        "brightsparkselectrical.uk",
        "bright-sparks-electrical.uk",
        "brightsparkselectrical.com",
        "bright-sparks-electrical.com",
    ]
    assert domain_candidates("Acme", "US") == ["acme.com", "acme.net", "acme.us"]
    assert page_mentions_business(
        "<h1>Bright Sparks Electrical — Leeds</h1>", "Bright Sparks Electrical Ltd"
    )
    assert not page_mentions_business("<h1>Domain for sale</h1>", "Bright Sparks Electrical Ltd")


def _fetcher(pages: dict[str, tuple[int, str]]) -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        status, body = pages.get(str(request.url), (404, "nope"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


async def _ctx(s: Any, pages: dict[str, tuple[int, str]], config: dict[str, Any]) -> SourceContext:
    settings = Settings(_env_file=None, demo_mode=True, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher(pages)
    return SourceContext(
        session=s,
        adapters=adapters,
        settings=settings,
        runtime=await runtime_settings.load(s),
        config=config,
    )


def _biz(name: str, **kw: Any) -> Business:
    return Business(
        name=name,
        normalized_name=name.lower(),
        industry="home_services",
        country=kw.pop("country", "GB"),
        **kw,
    )


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_site_health_creates_leads_for_broken_and_outdated_only() -> None:
    async with session_scope() as s:
        s.add_all(
            [
                _biz("Old Pipes", domain="oldpipes.co.uk"),
                _biz("Fresh Pipes", domain="freshpipes.co.uk"),
                _biz("Down Pipes", domain="downpipes.co.uk"),
                _biz("Elsewhere", domain="elsewhere.fr", country="FR"),
            ]
        )
        await s.flush()
        ctx = await _ctx(
            s,
            {
                "https://oldpipes.co.uk/": (200, OLD_SITE),
                "https://freshpipes.co.uk/": (200, NEW_SITE),
                "https://downpipes.co.uk/": (500, "error"),
            },
            {"per_run": 20, "recheck_days": 30},
        )
        leads = await SiteHealthSource().fetch(ctx)
        assert sorted(lead.title for lead in leads) == [
            "Down Pipes — website broken",
            "Old Pipes — website outdated",
        ]
        assert all(lead.trigger_type == TriggerType.BROKEN_OR_OUTDATED_SITE for lead in leads)
        statuses = {b.name: b.site_status for b in (await s.execute(select(Business))).scalars()}
        assert statuses == {
            "Old Pipes": "outdated",
            "Fresh Pipes": "ok",
            "Down Pipes": "broken",
            "Elsewhere": None,
        }  # FR is not a target country
        assert await SiteHealthSource().fetch(ctx) == []  # just checked: nothing re-scanned


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_no_website_check_finds_domain_or_profile() -> None:
    async with session_scope() as s:
        s.add_all(
            [
                _biz("Bright Sparks Electrical Ltd", address="Leeds LS1 1AA"),
                _biz("Quiet Roofing", address="York YO1 7HH"),
            ]
        )
        await s.flush()
        ctx = await _ctx(
            s,
            {
                "https://brightsparkselectrical.co.uk/": (200, "<h1>Bright Sparks Electrical</h1>"),
            },
            {"per_run": 10, "places_lookups": True},
        )
        leads = await NoWebsiteSource().fetch(ctx)
        found = (
            await s.execute(select(Business).where(Business.name.like("Bright%")))
        ).scalar_one()
        assert (found.domain, found.has_website) == ("brightsparkselectrical.co.uk", True)
        assert [lead.trigger_type for lead in leads] == [TriggerType.PROFILE_WITHOUT_SITE]
        assert leads[0].business and leads[0].business.name == "Quiet Roofing"


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_no_website_lead_without_places() -> None:
    async with session_scope() as s:
        s.add(_biz("Quiet Roofing", address="York YO1 7HH"))
        await s.flush()
        ctx = await _ctx(s, {}, {"per_run": 10, "places_lookups": False})
        leads = await NoWebsiteSource().fetch(ctx)
        assert [lead.trigger_type for lead in leads] == [TriggerType.NO_WEBSITE]
        biz = (await s.execute(select(Business))).scalar_one()
        assert biz.has_website is False and biz.site_checked_at is not None
