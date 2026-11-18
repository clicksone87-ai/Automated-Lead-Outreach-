"""Scout profiles source + link-in-bio contact extraction, with recorded pages (no network)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.adapters.search import SearchResult
from app.agents.contact_enrichment.agent import ContactEnrichmentAgent
from app.agents.discovery.base import SourceContext
from app.agents.discovery.sources.scout import ScoutProfilesSource
from app.config import Settings
from app.db.models import Business, Contact, ContactType, Lead, TriggerType
from app.db.session import session_scope
from app.orchestrator.agents import AgentContext
from app.services import runtime_settings
from tests.unit.test_scout_parsers import TIKTOK, YOUTUBE

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

HUB = (
    "<html><head><title>Leeds Plumbing Co</title></head><body>"
    '<a href="https://www.instagram.com/leedsplumbing">IG</a></body></html>'
)
PAGES = {
    "https://www.tiktok.com/@leedsplumbing": (200, TIKTOK),
    "https://www.youtube.com/@sparkel": (200, YOUTUBE),  # links its own site → skipped
    "https://bio.link/leedsplumbing": (200, HUB),
}
ROBOTS_BLOCKED = {"www.pinterest.com", "linktr.ee"}


class FakeSearch:
    name = "fake"

    def __init__(self, results: dict[str, list[SearchResult]]) -> None:
        self.results, self.queries = results, []

    async def search(self, session: Any, query: str, **kw: Any) -> list[SearchResult]:
        self.queries.append(query)
        site = query.split(maxsplit=1)[0]
        return self.results.get(site, [])


def _fetcher() -> SafeFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if request.url.host in ROBOTS_BLOCKED:
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(404)
        status, body = PAGES.get(str(request.url), (404, "nope"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


def _adapters(results: dict[str, list[SearchResult]]) -> Any:
    settings = Settings(_env_file=None, demo_mode=True, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher()
    adapters.search = FakeSearch(results)
    return settings, adapters


SEARCH = {
    "site:tiktok.com/@": [
        SearchResult(
            title="Leeds Plumbing Co (@leedsplumbing) | TikTok",
            url="https://www.tiktok.com/@leedsplumbing",
        ),
        # same account from a video link → one profile, read once
        SearchResult(title="video", url="https://www.tiktok.com/@leedsplumbing/video/1"),
    ],
    "site:youtube.com/@": [SearchResult(title="Spark", url="https://www.youtube.com/@sparkel")],
    "site:pinterest.com": [
        SearchResult(
            title="Pipe Pros Plumbing (pipepros) - Profile | Pinterest",
            url="https://www.pinterest.com/pipepros/",
            description="Emergency plumber in Leeds. Email hello@pipepros.example",
        )
    ],
}


async def test_scout_source_creates_profile_without_site_leads() -> None:
    settings, adapters = _adapters(SEARCH)
    async with session_scope() as s:
        rs = await runtime_settings.load(s)
        rs.target_countries, rs.active_industry = ["GB"], "home_services"
        ctx = SourceContext(
            session=s, adapters=adapters, settings=settings, runtime=rs,
            config={"platforms": ["tiktok", "youtube", "pinterest"], "queries_per_run": 3},
        )  # fmt: skip
        leads = await ScoutProfilesSource().fetch(ctx)

    assert [q.split()[0] for q in adapters.search.queries] == [
        "site:tiktok.com/@", "site:youtube.com/@", "site:pinterest.com",
    ]  # fmt: skip
    by_id = {lead.source_item_id: lead for lead in leads}
    assert set(by_id) == {"scout:tiktok:leedsplumbing", "scout:pinterest:pipepros"}
    tt = by_id["scout:tiktok:leedsplumbing"]
    assert tt.trigger_type == TriggerType.PROFILE_WITHOUT_SITE
    assert tt.business is not None and tt.business.email == "jobs@leedsplumbing.co.uk"
    assert tt.business.has_website is False and tt.business.country_hint == "GB"
    assert "https://bio.link/leedsplumbing" in tt.evidence_data["links"]
    assert "https://www.instagram.com/leedsplumbing" in tt.evidence_data["links"]  # from the hub
    pin = by_id["scout:pinterest:pipepros"]
    assert pin.evidence_data["via"] == "search"  # robots.txt blocks Pinterest pages
    assert pin.business is not None and pin.business.email == "hello@pipepros.example"
    assert ctx.state["stats"] == {"tiktok": 1, "youtube": 1, "pinterest": 1}


async def _profile_lead(s: Any, raw_text: str) -> Lead:
    biz = Business(name="Oslo Paint", normalized_name="oslo paint", country="NO")
    s.add(biz)
    await s.flush()
    lead = Lead(
        business_id=biz.id, source="scout_profiles", source_url="https://www.tiktok.com/@oslopaint",
        source_item_id=uuid.uuid4().hex, trigger_type="profile_without_site", country="NO",
        language="nb", raw_text=raw_text, pipeline_state={},
    )  # fmt: skip
    s.add(lead)
    await s.flush()
    return lead


async def test_link_in_bio_sets_website_and_profile_gets_a_dm_route() -> None:
    settings, adapters = _adapters({})
    PAGES["https://bio.link/oslopaint"] = (200, '<a href="https://oslopaint.no/">Nettside</a>')
    async with session_scope() as s:
        lead = await _profile_lead(s, "Bio: Maler i Oslo\nLinks: https://bio.link/oslopaint")
        outcome = await ContactEnrichmentAgent().run(
            AgentContext(session=s, adapters=adapters, settings=settings), lead.id
        )
        biz = await s.get(Business, lead.business_id)
        assert biz is not None and biz.domain == "oslopaint.no"
        # No email anywhere → the manual route is a DM on the profile (Track B drafts).
        assert outcome.proceed and "manual route" in outcome.reason
        contact = (
            (await s.execute(select(Contact).where(Contact.business_id == biz.id))).scalars().all()
        )
        assert [(c.type, c.value) for c in contact] == [
            (ContactType.PLATFORM_REPLY, "https://www.tiktok.com/@oslopaint")
        ]


async def test_robots_blocked_link_hub_is_never_read() -> None:
    settings, adapters = _adapters({})
    async with session_scope() as s:
        lead = await _profile_lead(s, "Links: https://linktr.ee/oslopaint")
        await ContactEnrichmentAgent().run(
            AgentContext(session=s, adapters=adapters, settings=settings), lead.id
        )
        biz = await s.get(Business, lead.business_id)
        assert biz is not None and biz.domain is None
