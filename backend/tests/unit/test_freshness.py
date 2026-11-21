"""The shared 8-hour rule: which URLs are social posts, which leads it applies to, and how
post times are read from search results."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.discovery.freshness import (
    is_fresh_post,
    is_social_post,
    is_social_url,
    parse_result_age,
    social_platform,
)
from app.config import Settings
from app.db.models import TriggerType

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.reddit.com/r/smallbusiness/comments/1abc/need_a_site/", "reddit"),
        ("https://old.reddit.com/r/Leeds/comments/1abc/", "reddit"),
        ("https://x.com/plumberjoe/status/2069", "x"),
        ("https://twitter.com/i/web/status/2069", "x"),
        ("https://www.instagram.com/p/ABC123/", "instagram"),
        ("https://www.instagram.com/reel/ABC123/", "instagram"),
        ("https://www.facebook.com/shop/posts/3540", "facebook"),
        ("https://m.facebook.com/groups/leedsbiz/permalink/123/", "facebook"),
        ("https://www.tiktok.com/@baker/video/71", "tiktok"),
        ("https://www.youtube.com/watch?v=cVE", "youtube"),
        ("https://youtu.be/cVE", "youtube"),
        ("https://www.youtube.com/shorts/cVE", "youtube"),
        ("https://www.threads.net/@scoops/post/DZZ", "threads"),
        ("https://www.linkedin.com/posts/owner_need-a-site-activity-75294983", "linkedin"),
        ("https://www.linkedin.com/feed/update/urn:li:activity:7529686502218502145", "linkedin"),
        ("https://www.pinterest.co.uk/pin/123456/", "pinterest"),
        ("https://github.com/acme/site/issues/12", "github"),
    ],
)
def test_post_urls_are_social(url: str, platform: str) -> None:
    assert social_platform(url) == platform and is_social_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@leedsplumbing",  # profile
        "https://www.instagram.com/leedsplumbing/",
        "https://www.youtube.com/@sparkel",
        "https://linktr.ee/leedsplumbing",
        "https://www.linkedin.com/in/samowner",
        "https://www.reddit.com/r/smallbusiness/",
        "https://forum.example.com/thread/1001",
        "https://www.bakery-leeds.co.uk/contact",
        "",
        None,
    ],
)
def test_profiles_and_ordinary_pages_are_not_posts(url: str | None) -> None:
    assert not is_social_url(url)


def test_rule_applies_to_social_requests_and_any_post_url_but_not_profiles() -> None:
    req, profile = TriggerType.EXPLICIT_REQUEST, TriggerType.PROFILE_WITHOUT_SITE
    assert is_social_post("social_providers", req, "https://example.com/a")
    assert is_social_post("social_facebook", req, "https://www.facebook.com/x/posts/1")
    assert is_social_post("rss", req, "https://www.reddit.com/r/a/comments/1abc/")
    assert is_social_post("search_api", req, "https://x.com/a/status/1")
    assert not is_social_post("search_api", req, "https://www.bakery.co.uk/blog/need-a-site")
    # Scout leads are profiles: a standing need with no post time.
    assert not is_social_post("scout_profiles", profile, "https://www.tiktok.com/@leedsplumbing")
    assert is_social_post("scout_profiles", profile, "https://www.tiktok.com/@a/video/9")


def test_fresh_means_dated_and_within_the_limit() -> None:
    s = Settings(_env_file=None, social_max_post_age_hours=8)  # type: ignore[call-arg]
    assert is_fresh_post(NOW - timedelta(hours=7, minutes=59), s, NOW)
    assert is_fresh_post(NOW - timedelta(hours=8), s, NOW)
    assert not is_fresh_post(NOW - timedelta(hours=8, seconds=1), s, NOW)
    assert not is_fresh_post(None, s, NOW)
    assert is_fresh_post((NOW - timedelta(hours=1)).replace(tzinfo=None), s, NOW)  # naive = UTC
    assert is_fresh_post(NOW + timedelta(minutes=10), s, NOW)  # small clock skew
    assert not is_fresh_post(NOW + timedelta(days=2), s, NOW)
    short = Settings(_env_file=None, social_max_post_age_hours=2)  # type: ignore[call-arg]
    assert not is_fresh_post(NOW - timedelta(hours=3), short, NOW)


def test_search_result_ages() -> None:
    assert parse_result_age("3 hours ago", NOW) == NOW - timedelta(hours=4)  # oldest it can be
    assert parse_result_age("an hour ago", NOW) == NOW - timedelta(hours=2)
    assert parse_result_age("45 mins ago", NOW) == NOW - timedelta(minutes=46)
    assert parse_result_age("2 days ago", NOW) == NOW - timedelta(days=3)
    assert parse_result_age("just now", NOW) == NOW
    assert parse_result_age("2026-11-21T09:30:00.000Z", NOW) == datetime(
        2026, 11, 21, 9, 30, tzinfo=UTC
    )
    assert parse_result_age("Nov 20, 2026", NOW) is None  # a bare date cannot prove 8 hours
    assert parse_result_age(None, NOW) is None and parse_result_age("soon", NOW) is None
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert is_fresh_post(parse_result_age("7 hours ago", NOW), s, NOW)
    assert not is_fresh_post(parse_result_age("8 hours ago", NOW), s, NOW)
