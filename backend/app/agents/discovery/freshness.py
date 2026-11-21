"""The 8-hour rule for social posts, in one place.

Every social post (found by any source, provider or search result) must carry its own
timestamp and be at most SOCIAL_MAX_POST_AGE_HOURS old; an undated post cannot prove it is
fresh, so it is dropped. Profile leads (a business account without a website) are not posts
and carry no post time, so the rule does not apply to them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from app.config import Settings, get_settings
from app.db.models import TriggerType
from app.services.families import SOCIAL, family_for

# A post on a social platform, as opposed to a profile, hashtag or search page.
_POST_PATTERNS: dict[str, str] = {
    "reddit": r"(?:^|\.)reddit\.com/(?:r|u|user)/[^/]+/comments/|(?:^|\.)redd\.it/\w",
    "x": r"(?:^|\.)(?:x|twitter)\.com/(?:[^/]+|i/web)/status/\d",
    "instagram": r"(?:^|\.)instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/[\w-]",
    "facebook": r"(?:^|\.)(?:facebook|fb)\.com/(?:.+/(?:posts|videos|permalink)/|"
    r"permalink\.php|story\.php|photo|watch|share/[prv]/|groups/[^/]+/(?:posts|permalink)/)",
    "tiktok": r"(?:^|\.)tiktok\.com/@[^/]+/(?:video|photo)/\d|(?:^|\.)vm\.tiktok\.com/\w",
    "youtube": r"(?:^|\.)youtube\.com/(?:watch\?|shorts/)|(?:^|\.)youtu\.be/[\w-]",
    "threads": r"(?:^|\.)threads\.(?:net|com)/@[^/]+/post/[\w-]",
    "linkedin": r"(?:^|\.)linkedin\.com/(?:posts/|feed/update/|pulse/)",
    "pinterest": r"(?:^|\.)pinterest\.[a-z.]+/pin/\d",
    "twitch": r"(?:^|\.)twitch\.tv/(?:videos/\d|[^/]+/clip/)",
    "github": r"(?:^|\.)github\.com/[^/]+/[^/]+/(?:issues|discussions)/\d",
}
_POST_RE = {k: re.compile(v, re.I) for k, v in _POST_PATTERNS.items()}
_SCHEME = re.compile(r"^[a-z]+://(?:www\.|m\.|mobile\.|old\.|new\.)?", re.I)

#: Clock skew tolerated for a post time slightly in the future.
FUTURE_SKEW = timedelta(minutes=60)


def social_platform(url: str | None) -> str | None:
    """The platform whose POST this URL is, or None (profiles and other pages)."""
    if not url:
        return None
    rest = _SCHEME.sub("", url.strip())
    for platform, rx in _POST_RE.items():
        if rx.search(rest):
            return platform
    return None


def is_social_url(url: str | None) -> bool:
    """True when the URL points to a post on a social platform."""
    return social_platform(url) is not None


def is_social_post(source: str, trigger_type: TriggerType | str, url: str | None) -> bool:
    """Does the 8-hour rule apply? Explicit requests from a social source, or any post URL."""
    if is_social_url(url):
        return True
    return family_for(source) == SOCIAL and str(trigger_type) == TriggerType.EXPLICIT_REQUEST


def max_post_age(settings: Settings | None = None) -> timedelta:
    return timedelta(hours=(settings or get_settings()).social_max_post_age_hours)


def is_fresh_post(
    posted_at: datetime | None, settings: Settings | None = None, now: datetime | None = None
) -> bool:
    """At most SOCIAL_MAX_POST_AGE_HOURS old at `now` (default: the current time).

    Undated posts are never fresh. A naive timestamp is read as UTC.
    """
    if posted_at is None:
        return False
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - posted_at
    return -FUTURE_SKEW <= age <= max_post_age(settings)


_RELATIVE = re.compile(
    r"^\s*(an?|\d+)\s*(second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago\s*$", re.I
)
_UNIT = {
    "second": timedelta(seconds=1),
    "sec": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=31),
    "year": timedelta(days=366),
}


def parse_result_age(value: str | None, now: datetime | None = None) -> datetime | None:
    """Post time from a search result's date field.

    Relative ages ("3 hours ago", Serper) count as the oldest time they can mean, so
    "8 hours ago" is never taken for fresh. ISO timestamps (Exa) are used as given. A bare
    calendar date ("Nov 20, 2026") cannot prove an 8-hour window and returns None.
    """
    if not value:
        return None
    now = now or datetime.now(UTC)
    s = value.strip()
    if s.lower() in ("just now", "now"):
        return now
    m = _RELATIVE.match(s)
    if m:
        n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
        return now - (n + 1) * _UNIT[m.group(2).lower()]
    if "T" not in s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
