"""Social data providers with recorded response shapes (EnsembleData, ScrapeCreators, SocialCrawl,
HikerAPI): parsing, per-provider daily caps, fallback, the 8-hour rule and the source. No live
calls; values are synthetic, structures follow responses captured on 2026-11-21."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from app.adapters.social import (
    EnsembleData,
    HikerApi,
    ScrapeCreators,
    SocialCrawl,
    SocialProviderError,
    SocialRouter,
    parse_time,
)
from app.agents.discovery.base import SourceContext
from app.agents.discovery.sources import SOURCES
from app.agents.discovery.sources.social_providers import SocialProvidersSource, build_router
from app.config import Settings, integration_status
from app.db.models import Cost, PolicyDecision
from app.db.session import session_scope
from app.orchestrator import quota
from app.services import runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

NOW = datetime.now(UTC)
FRESH = int((NOW - timedelta(hours=2)).timestamp())
STALE = int((NOW - timedelta(hours=30)).timestamp())


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


def aweme(vid: str, created: int, text: str = "I need a logo for my bakery") -> dict[str, Any]:
    return {"type": 1, "aweme_info": {
        "aweme_id": vid, "desc": text, "create_time": created, "region": "US",
        "share_url": f"https://www.tiktok.com/@baker/video/{vid}?_r=1",
        "author": {"unique_id": "baker", "nickname": "Baker"},
    }}  # fmt: skip


ED_TIKTOK = {"data": {"nextCursor": 20, "data": [aweme("71", FRESH), aweme("72", STALE)]}}
SC_REDDIT = {"success": True, "credits_remaining": 1299, "credits_charged": 1, "after": "t3_x",
             "posts": [{"id": "1abc", "title": "Need a website for my cafe", "selftext": "Budget $500",
                        "author": "cafeowner", "subreddit": "smallbusiness", "created_utc": FRESH,
                        "url": "https://www.reddit.com/r/smallbusiness/comments/1abc/x/",
                        "permalink": "/r/smallbusiness/comments/1abc/x/"}]}  # fmt: skip
SC_X = {"success": True, "credits_remaining": 1298, "credits_charged": 1, "query": "q", "cursor": "c",
        "posts": [{"__typename": "Tweet", "rest_id": "2069",
                   "core": {"user_results": {"result": {"core": {"screen_name": "plumberjoe"}}}},
                   "legacy": {"full_text": "Anyone know a web designer?", "lang": "en",
                              "created_at": datetime.fromtimestamp(FRESH, UTC).strftime(
                                  "%a %b %d %H:%M:%S +0000 %Y")},
                   "url": "https://x.com/plumberjoe/status/2069"}]}  # fmt: skip
SC_THREADS = {"success": True, "credits_remaining": 1297, "credits_charged": 1,
              "posts": [{"pk": "3916", "code": "DZZ", "taken_at": FRESH,
                         "user": {"username": "scoops"}, "caption": {"text": "I need logo design"}}]}  # fmt: skip
SC_YOUTUBE = {"success": True, "credits_remaining": 1296, "credits_charged": 1, "videos": [
    {"type": "video", "id": "cVE", "url": "https://www.youtube.com/watch?v=cVE",
     "title": "Need a website for my shop?", "channel": {"title": "Shop", "handle": "shopco"},
     "publishedTime": datetime.fromtimestamp(FRESH, UTC).isoformat().replace("+00:00", "Z")}]}  # fmt: skip
SC_IG_NONE = {"success": False, "credits_remaining": 1297, "credits_charged": 0,
              "error": "not_found", "errorStatus": 404, "message": "No posts found"}  # fmt: skip
SCW_FACEBOOK = {"success": True, "platform": "facebook", "credits_used": 1, "credits_remaining": 98,
                "cached": False, "data": {"items": [{"post": {
                    "id": "3540", "url": "https://www.facebook.com/shop/posts/3540",
                    "content": {"text": "Looking for someone to redo our website"},
                    "author": {"username": None, "display_name": "Corner Shop"},
                    "published_at": datetime.fromtimestamp(FRESH, UTC).isoformat()},
                    "computed": {"language": "en"}}], "next_cursor": None}}  # fmt: skip


def test_parse_time_formats() -> None:
    assert parse_time(FRESH) == parse_time(str(FRESH))
    assert parse_time("2026-11-20T15:28:20.275Z") == datetime(2026, 11, 20, 15, 28, 20, 275000, UTC)
    assert parse_time("Wed Jun 24 12:10:44 +0000 2026") == datetime(
        2026, 6, 24, 12, 10, 44, tzinfo=UTC
    )
    assert parse_time(None) is None and parse_time("nonsense") is None


@respx.mock
async def test_ensembledata_tiktok_parsing_and_usage() -> None:
    route = respx.get("https://ensembledata.com/apis/tt/keyword/search").mock(
        return_value=httpx.Response(200, json=ED_TIKTOK)
    )
    async with session_scope() as s:
        posts = await EnsembleData("tok").search(s, "tiktok", "need a logo", agent="d")
    params = route.calls[0].request.url.params
    assert params["token"] == "tok" and params["name"] == "need a logo" and params["period"] == "1"
    assert [p.id for p in posts] == ["71", "72"]
    assert posts[0].url == "https://www.tiktok.com/@baker/video/71"  # tracking query dropped
    assert posts[0].author == "baker" and posts[0].text.startswith("I need a logo")
    async with session_scope() as s:
        assert (await s.execute(select(Cost.provider))).scalars().all() == ["ensembledata"]


@respx.mock
async def test_scrapecreators_platforms() -> None:
    base = "https://api.scrapecreators.com/v1"
    respx.get(f"{base}/reddit/search").mock(return_value=httpx.Response(200, json=SC_REDDIT))
    respx.get(f"{base}/twitter/search/posts").mock(return_value=httpx.Response(200, json=SC_X))
    respx.get(f"{base}/threads/search").mock(return_value=httpx.Response(200, json=SC_THREADS))
    respx.get(f"{base}/youtube/search").mock(return_value=httpx.Response(200, json=SC_YOUTUBE))
    ig = respx.get(f"{base}/instagram/search/hashtag").mock(
        return_value=httpx.Response(404, json=SC_IG_NONE)
    )
    sc = ScrapeCreators("sk")
    async with session_scope() as s:
        reddit = await sc.search(s, "reddit", "need a website", agent="d")
        x = await sc.search(s, "x", "need a website", agent="d")
        threads = await sc.search(s, "threads", "need a logo", agent="d")
        yt = await sc.search(s, "youtube", "need a website", agent="d")
        insta = await sc.search(s, "instagram", "#need a website", agent="d")
    assert reddit[0].text == "Need a website for my cafe\n\nBudget $500"
    assert reddit[0].extra == {"subreddit": "smallbusiness"} and reddit[0].posted_at is not None
    assert x[0].author == "plumberjoe" and x[0].posted_at == parse_time(FRESH)
    assert threads[0].url == "https://www.threads.net/@scoops/post/DZZ"
    assert yt[0].author == "shopco" and yt[0].posted_at is not None
    assert insta == [] and ig.calls[0].request.url.params["hashtag"] == "needawebsite"
    assert ig.calls[0].request.headers["x-api-key"] == "sk"


@respx.mock
async def test_socialcrawl_unified_schema() -> None:
    route = respx.get("https://www.socialcrawl.dev/v1/facebook/search/posts").mock(
        return_value=httpx.Response(200, json=SCW_FACEBOOK)
    )
    async with session_scope() as s:
        posts = await SocialCrawl("ck").search(s, "facebook", "need a website", agent="d")
    assert route.calls[0].request.headers["x-api-key"] == "ck"
    assert "ck" not in str(route.calls[0].request.url)
    p = posts[0]
    assert (p.platform, p.id, p.author) == ("facebook", "3540", "Corner Shop")
    assert p.extra == {"language": "en"}


@respx.mock
async def test_hikerapi_uses_access_key_header() -> None:
    route = respx.get("https://api.instagrapi.com/g2/hashtag/medias").mock(
        return_value=httpx.Response(200, json={"items": [
            {"pk": "1", "code": "ABC", "taken_at": FRESH, "caption": {"text": "need a logo"},
             "user": {"username": "shop"}}]})
    )  # fmt: skip
    async with session_scope() as s:
        posts = await HikerApi("hk", "https://api.instagrapi.com").search(
            s, "instagram", "needalogo", agent="d"
        )
    assert route.calls[0].request.headers["x-access-key"] == "hk"
    assert posts[0].url == "https://www.instagram.com/p/ABC/" and posts[0].author == "shop"


@respx.mock
async def test_router_falls_back_on_error_and_daily_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    capped = _settings(social_provider_daily_cap=1)
    monkeypatch.setattr(quota, "get_settings", lambda: capped)
    respx.get("https://ensembledata.com/apis/tt/keyword/search").mock(
        return_value=httpx.Response(495, json={"detail": "All daily units used"})
    )
    respx.get("https://www.socialcrawl.dev/v1/tiktok/search").mock(
        return_value=httpx.Response(200, json={"success": True, "data": {"items": [
            {"post": {"id": "9", "url": "https://www.tiktok.com/@a/video/9",
                      "content": {"text": "need a site"}, "author": {"username": "a"},
                      "published_at": datetime.fromtimestamp(FRESH, UTC).isoformat()}}]}})
    )  # fmt: skip
    respx.get("https://api.scrapecreators.com/v1/tiktok/search/keyword").mock(
        return_value=httpx.Response(
            200, json={"success": True, "search_item_list": [aweme("5", FRESH)]}
        )
    )
    router = SocialRouter([EnsembleData("e"), SocialCrawl("c"), ScrapeCreators("s")])
    async with session_scope() as s:
        first = await router.search(s, "tiktok", "need a site", agent="d")
        second = await router.search(s, "tiktok", "need a site", agent="d")
    assert first[0].provider == "socialcrawl"  # EnsembleData answered 495
    assert second[0].provider == "scrapecreators"  # both others at their cap of 1/day
    async with session_scope() as s:
        errs = (await s.execute(select(PolicyDecision.action).where(
            PolicyDecision.check == "provider_error"))).scalars().all()  # fmt: skip
    assert errs[0] == "ensembledata" and "socialcrawl" in errs
    with pytest.raises(SocialProviderError):
        async with session_scope() as s:
            await SocialRouter([SocialCrawl("c")]).search(s, "tiktok", "q", agent="d")


def test_integration_status_and_registration() -> None:
    st = integration_status(_settings(scrapecreators_api_key="k"))
    assert st["social_providers"].configured
    assert not integration_status(_settings())["social_providers"].configured
    assert "social_providers" in SOURCES and SOURCES["social_providers"].default_enabled
    router = build_router(_settings(scrapecreators_api_key="k", ensembledata_api_token="t"))
    assert set(router.platforms()) == {"tiktok", "reddit", "x", "threads", "youtube", "instagram"}


@respx.mock
async def test_source_keeps_only_fresh_posts_and_rotates(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("https://ensembledata.com/apis/tt/keyword/search").mock(
        return_value=httpx.Response(200, json=ED_TIKTOK)
    )
    respx.get("https://api.scrapecreators.com/v1/reddit/search").mock(
        return_value=httpx.Response(200, json=SC_REDDIT)
    )
    settings = _settings(
        ensembledata_api_token="t", scrapecreators_api_key="k",
        social_platforms="tiktok,reddit,linkedin", social_max_post_age_hours=8,
    )  # fmt: skip
    src = SocialProvidersSource()
    async with session_scope() as s:
        rs = await runtime_settings.load(s)
        ctx = SourceContext(s, None, settings, rs, {"platforms_per_run": 2})  # type: ignore[arg-type]
        assert src.platforms(ctx, build_router(settings)) == ["tiktok", "reddit"]  # never linkedin
        leads = await src.fetch(ctx)
    ids = sorted(lead.source_item_id for lead in leads)
    assert ids == ["reddit:1abc", "tiktok:71"]  # tiktok:72 is 30 hours old
    tiktok = next(lead for lead in leads if lead.source_item_id == "tiktok:71")
    assert tiktok.evidence_data["provider"] == "ensembledata"
    assert tiktok.evidence_data["platform"] == "tiktok" and tiktok.author == "baker"
    assert ctx.state["stats"]["tiktok"] == {"seen": 2, "fresh": 1, "query": "need a website"}
    off = _settings(ensembledata_api_token="t", social_sources_enabled=False)
    async with session_scope() as s:
        ctx = SourceContext(s, None, off, rs, {})  # type: ignore[arg-type]
        assert await src.fetch(ctx) == []
