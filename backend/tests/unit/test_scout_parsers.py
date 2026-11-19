"""Scout profile parsers (ported from github.com/kiryano/Scout) — pure functions."""

from __future__ import annotations

import json

import pytest

from app.agents.scout import parsers

TIKTOK = (
    '<html><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
    + json.dumps(
        {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "userInfo": {
                        "user": {
                            "uniqueId": "leedsplumbing",
                            "nickname": "Leeds Plumbing Co",
                            "signature": "Boilers & bathrooms 🔧 bookings: jobs@leedsplumbing.co.uk",
                            "verified": False,
                            "bioLink": {"link": "bio.link/leedsplumbing"},
                        },
                        "stats": {"followerCount": 1520, "heartCount": 30000, "videoCount": 41},
                    }
                }
            }
        }
    )
    + "</script></html>"
)
YOUTUBE = (
    '{"channelMetadataRenderer":{"title":"Spark Electrical \\u00e6","description":"Electrician in '
    'Bergen \\nContact: post@spark.no"}} "canonicalChannelUrl":"https://www.youtube.com/@sparkel" '
    '"subscriberCountText":{"simpleText":"12.5K subscribers"} "channelId":"UC1234567890123456789012" '
    '"urlEndpoint":{"url":"https://www.youtube.com/redirect?q=https%3A%2F%2Fspark-el.no%2F&v=1"}'
)


@pytest.mark.parametrize(
    ("platform", "url", "expected"),
    [
        ("tiktok", "https://www.tiktok.com/@leeds.plumbing", "leeds.plumbing"),
        ("tiktok", "https://www.tiktok.com/@f5.plumbing/video/123", "f5.plumbing"),
        ("tiktok", "https://www.tiktok.com/@abc/photo/1?image_index=3", "abc"),
        ("tiktok", "https://www.tiktok.com/discover/plumber", None),
        ("youtube", "https://www.youtube.com/@SparkEl/shorts", "SparkEl"),
        ("youtube", "https://www.youtube.com/@SparkEl/about", "SparkEl"),
        ("instagram", "https://www.instagram.com/p/Cabc123/", None),
        ("instagram", "https://www.instagram.com/bergen_roofing/", "bergen_roofing"),
        ("pinterest", "https://www.pinterest.co.uk/tilerstudio/", "tilerstudio"),
        ("pinterest", "https://www.pinterest.com/pin/12345/", None),
        ("linktree", "https://linktr.ee/oslo.paint", "oslo.paint"),
        ("twitch", "https://www.twitch.tv/directory", None),
        ("github", "https://github.com/octocat", "octocat"),
        ("github", "https://github.com/octocat/hello-world", None),
    ],
)
def test_username_from_url(platform: str, url: str, expected: str | None) -> None:
    assert parsers.username_from_url(platform, url) == expected


def test_helpers() -> None:
    assert parsers.parse_count("11.5K") == 11_500
    assert parsers.parse_count("2M") == 2_000_000
    assert parsers.parse_count("bad") == 0
    links = ["https://instagram.com/x", "https://linktr.ee/x", "https://www.bobsroofing.co.uk/a"]
    assert parsers.own_website(links) == "https://bobsroofing.co.uk/"
    assert parsers.is_link_hub("https://bio.link/x") and not parsers.is_link_hub("https://a.com")


def test_parse_tiktok() -> None:
    p = parsers.parse_tiktok(TIKTOK, "leedsplumbing")
    assert p is not None
    assert (p.username, p.full_name, p.follower_count) == (
        "leedsplumbing",
        "Leeds Plumbing Co",
        1520,
    )
    assert p.email == "jobs@leedsplumbing.co.uk"
    assert p.links == ["https://bio.link/leedsplumbing"] and p.website == ""  # a hub, not a site
    assert parsers.parse_tiktok("<html></html>", "x") is None


def test_parse_youtube_unescapes_and_unwraps_redirects() -> None:
    p = parsers.parse_youtube(YOUTUBE, "@sparkel")
    assert p is not None
    assert p.full_name == "Spark Electrical æ" and p.username == "sparkel"
    assert p.email == "post@spark.no" and p.follower_count == 12_500
    assert p.website == "https://spark-el.no/"
    assert p.extra["channel_id"] == "UC1234567890123456789012"


def test_parse_link_hub_linktree_and_generic() -> None:
    nxt = {"props": {"pageProps": {"account": {"pageTitle": "Oslo Paint", "description": "Maler",
           "links": [{"url": "https://www.instagram.com/oslopaint"}, {"url": "mailto:hei@oslopaint.no"}]}}}}  # fmt: skip
    html = f'<script id="__NEXT_DATA__">{json.dumps(nxt)}</script>'
    p = parsers.parse_link_hub(html, "https://linktr.ee/oslopaint")
    assert p is not None and p.full_name == "Oslo Paint" and p.email == "hei@oslopaint.no"
    assert p.website == "" and p.extra["socials"] == {
        "instagram": "https://www.instagram.com/oslopaint"
    }
    generic = (
        '<html><head><title>Bob Roofing</title><meta name="description" content="Roofer"></head>'
        '<body><a href="https://bobroofing.ie/">Site</a><a href="tel:+353 1 234 5678">Call</a>'
        '<a href="https://bio.link/static/x.css">x</a></body></html>'
    )
    g = parsers.parse_link_hub(generic, "https://bio.link/bobroofing")
    assert g is not None and g.website == "https://bobroofing.ie/" and g.phone == "+35312345678"
    assert g.username == "bobroofing" and g.full_name == "Bob Roofing"


def test_parse_github_twitch_instagram() -> None:
    gh = parsers.parse_github(
        {
            "login": "octo",
            "name": "Octo Builds",
            "bio": "Carpenter",
            "blog": "octobuilds.com",
            "followers": 9,
            "html_url": "https://github.com/octo",
        }
    )
    assert gh is not None and gh.website == "https://octobuilds.com/" and gh.via == "api"
    assert parsers.parse_github({"login": "empty"}) is None
    tw = parsers.parse_twitch(
        {"login": "diyandy", "display_name": "DIY Andy", "description": "Biz: andy@diy.tv"}, 300
    )
    assert tw is not None and tw.email == "andy@diy.tv" and tw.follower_count == 300
    ig = parsers.parse_instagram_apify(
        {
            "username": "bergen_roofing",
            "biography": "Tak",
            "businessEmail": "tak@bergen.no",
            "externalUrl": "https://linktr.ee/bergen",
            "followersCount": 800,
        },
        "bergen_roofing",
    )
    assert ig is not None and ig.email == "tak@bergen.no" and ig.website == "" and ig.via == "apify"


def test_profile_from_search_snippet() -> None:
    p = parsers.profile_from_search(
        "pinterest",
        "https://www.pinterest.com/tilerstudio/",
        "Tiler Studio (tilerstudio) - Profile | Pinterest",
        "Bathroom tiling in Leeds. Email hello@tilerstudio.co.uk or visit tilerstudio.co.uk",
    )
    assert p is not None and p.via == "search"
    assert p.full_name == "Tiler Studio" and p.email == "hello@tilerstudio.co.uk"
    assert p.website == "https://tilerstudio.co.uk/"
    assert (
        parsers.profile_from_search("pinterest", "https://www.pinterest.com/pin/1/", "", "") is None
    )


def test_video_hit_does_not_borrow_the_post_title() -> None:
    p = parsers.profile_from_search(
        "tiktok",
        "https://www.tiktok.com/@f5.plumbing/video/1",
        "Sewer Stoppage Cleared",
        "90' cable",
    )
    assert p is not None and p.full_name == "" and p.bio == "" and p.extra["from_post"]


def test_relevance_needs_the_trade_on_the_account() -> None:
    from app.agents.discovery.sources.scout import relevant  # noqa: PLC0415

    def prof(username: str, **kw: object) -> parsers.Profile:
        return parsers.Profile(platform="tiktok", username=username, profile_url="u", **kw)  # type: ignore[arg-type]

    assert relevant(prof("allstarplumbingabilene"), "plumber")
    assert relevant(prof("f5.plumbing.abi.tx"), "plumber")
    assert not relevant(prof("breakingwithbri"), "plumber")
    assert relevant(prof("osloab", full_name="Oslo Rørleggerservice"), "rørlegger")
    assert relevant(prof("bobco", bio="Roofing in Leeds"), "roofer")
    assert not relevant(prof("bobco", bio="Roofing tips", extra={"from_post": True}), "roofer")
