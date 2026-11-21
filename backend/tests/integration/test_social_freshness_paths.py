"""The 8-hour rule on every social path: Apify Facebook and Instagram, each social provider,
Scout, search results that point at a social post, and the runner's ingestion choke point.
Each path gets a fresh, a stale and an undated post; only the fresh one may become a lead."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.adapters.registry import get_adapters
from app.adapters.search import SearchResult
from app.agents.discovery import runner
from app.agents.discovery.base import RawBusiness, RawLead, SourceContext
from app.agents.discovery.sources import social_apify
from app.agents.discovery.sources.scout import ScoutProfilesSource
from app.agents.discovery.sources.search_api import SearchApiSource
from app.agents.discovery.sources.social_apify import ApifyFacebookSource, ApifyInstagramSource
from app.agents.discovery.sources.social_providers import SocialProvidersSource
from app.config import Settings
from app.db.models import EvidenceKind, Lead, TriggerType
from app.db.session import session_scope
from app.services import runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

NOW = datetime.now(UTC)
FRESH_DT, STALE_DT = NOW - timedelta(hours=2), NOW - timedelta(hours=30)
FRESH, STALE = int(FRESH_DT.timestamp()), int(STALE_DT.timestamp())
ALL = "instagram,tiktok,facebook,x,reddit,youtube,linkedin,pinterest,threads,twitch,github,linktree"


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


async def _ctx(s: Any, settings: Settings, config: dict[str, Any], **kw: Any) -> SourceContext:
    rs = await runtime_settings.load(s)
    return SourceContext(s, kw.get("adapters"), settings, rs, config)  # type: ignore[arg-type]


# ── Apify (Facebook and Instagram only) ────────────────────────────────────


async def test_apify_facebook_keeps_only_fresh_dated_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(ctx: Any, actor: str, actor_input: dict[str, Any], *, source: str) -> Any:
        return [
            {"postId": "f", "url": "https://www.facebook.com/a/posts/f", "text": "Need a website",
             "time": FRESH_DT.isoformat()},
            {"postId": "s", "url": "https://www.facebook.com/a/posts/s", "text": "Need a website",
             "time": STALE_DT.isoformat()},
            {"postId": "u", "url": "https://www.facebook.com/a/posts/u", "text": "Need a website"},
        ]  # fmt: skip

    monkeypatch.setattr(social_apify, "_run", fake_run)
    async with session_scope() as s:
        leads = await ApifyFacebookSource().fetch(await _ctx(s, _settings(), {"max_items": 8}))
    assert [lead.source_item_id for lead in leads] == ["facebook:f"]


async def test_apify_instagram_keeps_only_fresh_dated_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(ctx: Any, actor: str, actor_input: dict[str, Any], *, source: str) -> Any:
        return [
            {"id": "f", "url": "https://www.instagram.com/p/f/", "caption": "need a logo",
             "timestamp": FRESH_DT.isoformat()},
            {"id": "s", "url": "https://www.instagram.com/p/s/", "caption": "need a logo",
             "timestamp": STALE_DT.isoformat()},
            {"id": "u", "url": "https://www.instagram.com/p/u/", "caption": "need a logo"},
        ]  # fmt: skip

    monkeypatch.setattr(social_apify, "_run", fake_run)
    async with session_scope() as s:
        leads = await ApifyInstagramSource().fetch(await _ctx(s, _settings(), {}))
    assert [lead.source_item_id for lead in leads] == ["instagram:f"]


# ── every licensed social provider ─────────────────────────────────────────


def _aweme(vid: str, created: int | None) -> dict[str, Any]:
    return {"aweme_info": {"aweme_id": vid, "desc": "need a logo", "create_time": created,
                           "author": {"unique_id": "baker"}}}  # fmt: skip


def _scw(pid: str, url: str, published: int | None) -> dict[str, Any]:
    when = datetime.fromtimestamp(published, UTC).isoformat() if published else None
    return {"post": {"id": pid, "url": url, "content": {"text": "need a website"},
                     "author": {"username": "owner"}, "published_at": when}}  # fmt: skip


def _iso(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


TRIPLE = (("f", FRESH), ("s", STALE), ("u", None))
SC = "https://api.scrapecreators.com/v1"
SCW = "https://www.socialcrawl.dev/v1"
PROVIDER_ROUTES: dict[str, tuple[str, dict[str, Any]]] = {
    "tiktok": ("https://ensembledata.com/apis/tt/keyword/search",
               {"data": {"data": [_aweme(i, t) for i, t in TRIPLE]}}),
    "reddit": (f"{SC}/reddit/search", {"success": True, "posts": [
        {"id": i, "title": "Need a website", "created_utc": t,
         "url": f"https://www.reddit.com/r/smallbusiness/comments/{i}/x/"} for i, t in TRIPLE]}),
    "threads": (f"{SC}/threads/search", {"success": True, "posts": [
        {"pk": i, "code": i, "taken_at": t, "user": {"username": "u"},
         "caption": {"text": "need a logo"}} for i, t in TRIPLE]}),
    "youtube": (f"{SC}/youtube/search", {"success": True, "videos": [
        {"id": i, "title": "need a website", "publishedTime": _iso(t)} for i, t in TRIPLE]}),
    "linkedin": (f"{SC}/linkedin/search/posts", {"success": True, "posts": [
        {"activityUrn": f"urn:li:activity:752949836248472{n}",
         "url": f"https://www.linkedin.com/feed/update/urn:li:activity:752949836248472{n}",
         "description": "need a website", "datePublished": _iso(t), "author": {"name": "A"}}
        for n, (_, t) in enumerate(TRIPLE)]}),
    "x": (f"{SCW}/twitter/search/tweets", {"success": True, "data": {"items": [
        _scw(i, f"https://x.com/a/status/{n}", t) for n, (i, t) in enumerate(TRIPLE)]}}),
    "facebook": (f"{SCW}/facebook/search/posts", {"success": True, "data": {"items": [
        _scw(i, f"https://www.facebook.com/a/posts/{i}", t) for i, t in TRIPLE]}}),
    "instagram": ("https://api.instagrapi.com/g2/hashtag/medias", {"items": [
        {"pk": i, "code": i, "taken_at": t, "caption": {"text": "need a logo"}} for i, t in TRIPLE]}),
}  # fmt: skip


@respx.mock
async def test_every_provider_platform_drops_stale_and_undated_posts() -> None:
    for url, body in PROVIDER_ROUTES.values():
        respx.get(url).mock(return_value=httpx.Response(200, json=body))
    settings = _settings(
        ensembledata_api_token="e", scrapecreators_api_key="s", socialcrawl_api_key="c",
        hikerapi_access_key="h", social_platforms=ALL,
    )  # fmt: skip
    async with session_scope() as s:
        ctx = await _ctx(s, settings, {"platforms_per_run": 8})
        leads = await SocialProvidersSource().fetch(ctx)
    by_platform = {lead.evidence_data["platform"]: lead for lead in leads}
    assert set(by_platform) == set(PROVIDER_ROUTES)  # every platform answered once
    assert len(leads) == len(PROVIDER_ROUTES)  # one fresh post each
    assert all(lead.posted_at and lead.posted_at >= FRESH_DT - timedelta(seconds=1)
               for lead in leads)  # fmt: skip
    assert {lead.evidence_data["provider"] for lead in leads} == {
        "ensembledata", "scrapecreators", "socialcrawl", "hikerapi"
    }  # fmt: skip
    assert all(v["seen"] == 3 and v["fresh"] == 1 for v in ctx.state["stats"].values())


# ── search results that point at a social post ─────────────────────────────


class _Search:
    name: ClassVar[str] = "fake"
    is_mock: ClassVar[bool] = True  # no page fetch: the snippet is the text

    async def search(self, session: Any, query: str, **kw: Any) -> list[SearchResult]:
        text = "Looking for a web designer to build a new website for my plumbing business"
        return [
            SearchResult(title="r/Leeds", url="https://www.reddit.com/r/Leeds/comments/f1/x/",
                         description=text, age="2 hours ago"),
            SearchResult(title="r/Leeds", url="https://www.reddit.com/r/Leeds/comments/s1/x/",
                         description=text, age="9 hours ago"),
            SearchResult(title="tweet", url="https://x.com/joe/status/123",
                         description=text, age=None),
            SearchResult(title="blog", url="https://www.example-plumbing.co.uk/news/new-site",
                         description=text, age=None),
        ]  # fmt: skip


async def test_search_results_pointing_at_social_posts_need_a_fresh_date() -> None:
    adapters = dataclasses.replace(get_adapters(), search=_Search())
    async with session_scope() as s:
        ctx = await _ctx(s, _settings(), {"queries_per_run": 1}, adapters=adapters)
        leads = await SearchApiSource().fetch(ctx)
    urls = {lead.source_url: lead for lead in leads}
    assert set(urls) == {
        "https://www.reddit.com/r/Leeds/comments/f1/x/",
        "https://www.example-plumbing.co.uk/news/new-site",  # not a social post: no date needed
    }
    assert urls["https://www.reddit.com/r/Leeds/comments/f1/x/"].posted_at is not None
    assert urls["https://www.example-plumbing.co.uk/news/new-site"].posted_at is None


# ── Scout profiles and the runner's choke point ────────────────────────────


def _item(url: str, posted: datetime | None, **kw: Any) -> RawLead:
    return RawLead(source_item_id=uuid.uuid4().hex, source_url=url,
                   trigger_type=kw.pop("trigger", TriggerType.EXPLICIT_REQUEST),
                   title="Need a website", text="Need a website for my shop", posted_at=posted,
                   **kw)  # fmt: skip


async def _ingest(key: str, item: RawLead) -> uuid.UUID | None:
    from app.agents.discovery.sources import SOURCES  # noqa: PLC0415

    async with session_scope() as s:
        return await runner.ingest(
            s, get_adapters(), SOURCES[key], item, uses_mock=False, settings=_settings()
        )


async def test_scout_profile_leads_are_exempt_but_post_urls_are_not() -> None:
    profile = _item(
        "https://www.tiktok.com/@leedsplumbing", None, trigger=TriggerType.PROFILE_WITHOUT_SITE,
        business=RawBusiness(name="Leeds Plumbing", has_website=False),
        evidence_kind=EvidenceKind.PAGE,
    )  # fmt: skip
    assert ScoutProfilesSource.key == "scout_profiles"
    assert await _ingest("scout_profiles", profile) is not None
    post = _item("https://www.tiktok.com/@leedsplumbing/video/71", None,
                 trigger=TriggerType.PROFILE_WITHOUT_SITE)  # fmt: skip
    assert await _ingest("scout_profiles", post) is None


async def test_runner_choke_point_holds_every_source_to_the_rule() -> None:
    social = "social_providers"
    assert (
        await _ingest(social, _item("https://x.com/a/status/1", NOW - timedelta(hours=9))) is None
    )
    assert await _ingest(social, _item("https://x.com/a/status/2", None)) is None
    fresh = await _ingest(social, _item("https://x.com/a/status/3", FRESH_DT))
    assert fresh is not None
    # A non-social source is held to it as soon as its URL is a social post.
    assert await _ingest("rss", _item("https://www.reddit.com/r/a/comments/9/x/", None)) is None
    assert await _ingest("rss", _item("https://blog.example.com/need-a-site", None)) is not None
    async with session_scope() as s:
        leads = (await s.execute(select(Lead.source_url, Lead.source_family))).all()
        assert (await s.execute(select(func.count()).select_from(Lead))).scalar_one() == 2
    assert ("https://x.com/a/status/3", "social") in leads
