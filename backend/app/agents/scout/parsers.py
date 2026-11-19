"""Creator and business profile parsers, ported from Scout.

Source: https://github.com/kiryano/Scout (MIT License, Copyright (c) 2026 Scout).
Pure functions only — HTML/JSON in, `Profile` out. Fetching lives in `client.py`, which uses the
safe fetcher (robots.txt, honest user agent, rate limits) or official APIs.

Deliberately NOT ported (V2: "risky features off by default"): user-agent rotation, proxy and
free-proxy support, the LinkedIn session-cookie scraper, email-pattern guessing and SMTP probing.
Emails come only from what a profile publishes and are verified like every other email.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from app.agents.contact_enrichment.extract import extract_emails
from app.services.dedup import normalize_domain

PLATFORMS: tuple[str, ...] = (
    "tiktok",
    "youtube",
    "instagram",
    "pinterest",
    "linktree",
    "twitch",
    "github",
)
LABELS = {
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "pinterest": "Pinterest",
    "linktree": "Linktree",
    "twitch": "Twitch",
    "github": "GitHub",
    "link_hub": "Link in bio",
}
#: Link-in-bio hosts. Their pages list a creator's other links, email and website.
LINK_HUBS = frozenset(
    {
        "linktr.ee",
        "linkin.bio",
        "beacons.ai",
        "lnk.bio",
        "taplink.cc",
        "bio.link",
        "stan.store",
        "linkr.bio",
    }
)
SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "twitter.com": "x",
    "x.com": "x",
    "facebook.com": "facebook",
    "linkedin.com": "linkedin",
    "pinterest.com": "pinterest",
    "twitch.tv": "twitch",
    "github.com": "github",
    "wa.me": "whatsapp",
}


@dataclass(slots=True)
class Profile:
    platform: str
    username: str
    profile_url: str
    full_name: str = ""
    bio: str = ""
    email: str = ""
    website: str = ""  # the business's own site (never a platform or link hub)
    links: list[str] = field(default_factory=list)
    follower_count: int = 0
    phone: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    #: "page" (profile page parsed), "api" (official API), "apify", "search" (snippet only)
    via: str = "page"


# ── shared helpers (Scout utils.py) ──────────────────────────────────────


def parse_count(value: str | int | None) -> int:
    """'11.5K' → 11500, '2.3M' → 2300000 (Scout parse_abbreviated_number)."""
    if isinstance(value, int):
        return value
    s = str(value or "").strip().replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(s[-1:].upper(), 1)
    try:
        return int(float(s[:-1] if mult > 1 else s) * mult)
    except ValueError:
        return 0


def first_email(*texts: str) -> str:
    for text in texts:
        if text:
            found = extract_emails(text)
            if found:
                return found[0].email
    return ""


_PHONE = re.compile(r"(?:tel:|\+)[\d\s().-]{9,20}\d")


def first_phone(*texts: str) -> str:
    for text in texts:
        m = _PHONE.search(text or "")
        if m:
            digits = re.sub(r"[^\d+]", "", m.group(0).removeprefix("tel:"))
            if len(digits.lstrip("+")) >= 9:
                return digits
    return ""


def is_link_hub(url: str) -> bool:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return host in LINK_HUBS


def own_website(links: list[str]) -> str:
    """First link that is the business's own site: not a platform, not a link hub."""
    for link in links:
        if not link.startswith(("http://", "https://")) or is_link_hub(link):
            continue
        domain = normalize_domain(link)
        if domain:
            return f"https://{domain}/"
    return ""


def socials(links: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for link in links:
        host = (urlparse(link).hostname or "").removeprefix("www.")
        name = SOCIAL_HOSTS.get(host)
        if name and name not in out:
            out[name] = link
    return out


def _unescape_json_string(raw: str) -> str:
    try:
        return str(json.loads(f'"{raw}"'))
    except json.JSONDecodeError:
        return raw


def _dedupe(links: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for link in links:
        if link and link not in seen:
            seen.add(link)
            out.append(link)
    return out


# ── profile URLs → usernames ─────────────────────────────────────────────

_RESERVED = {
    "twitch": {"directory", "videos", "p", "search", "downloads", "jobs", "turbo", "settings"},
    "github": {"orgs", "topics", "features", "about", "explore", "marketplace", "sponsors",
               "login", "pricing", "enterprise", "collections", "search", "trending"},
    "pinterest": {"pin", "search", "ideas", "today", "business", "categories", "_"},
    "instagram": {"p", "reel", "reels", "explore", "stories", "accounts", "tv", "about"},
}  # fmt: skip
_URL_PATTERNS = {
    # Video/photo/short URLs carry the account too: /@handle/video/123 → handle.
    "tiktok": re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,24})(?:[/?#]|$)"),
    "youtube": re.compile(r"youtube\.com/@([A-Za-z0-9_.-]{3,30})(?:[/?#]|$)"),
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?(?:\?.*)?$"),
    "pinterest": re.compile(r"pinterest\.[a-z.]+/([A-Za-z0-9_]{3,30})/?(?:\?.*)?$"),
    "linktree": re.compile(r"linktr\.ee/([A-Za-z0-9_.]{2,40})/?(?:\?.*)?$"),
    "twitch": re.compile(r"twitch\.tv/([A-Za-z0-9_]{4,25})/?(?:about)?/?(?:\?.*)?$"),
    "github": re.compile(
        r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?(?:\?.*)?$"
    ),
}


def username_from_url(platform: str, url: str) -> str | None:
    """The profile's username, or None when the URL is a post, video, board or reserved page."""
    pattern = _URL_PATTERNS.get(platform)
    m = pattern.search(url) if pattern else None
    if not m:
        return None
    name = m.group(1)
    if name.lower() in _RESERVED.get(platform, set()):
        return None
    return name


def profile_url(platform: str, username: str) -> str:
    return {
        "tiktok": f"https://www.tiktok.com/@{username}",
        "youtube": f"https://www.youtube.com/@{username}",
        "instagram": f"https://www.instagram.com/{username}/",
        "pinterest": f"https://www.pinterest.com/{username}/",
        "linktree": f"https://linktr.ee/{username}",
        "twitch": f"https://www.twitch.tv/{username}",
        "github": f"https://github.com/{username}",
    }[platform]


# ── per platform ─────────────────────────────────────────────────────────


def parse_tiktok(html: str, username: str) -> Profile | None:
    """TikTok profile page: the embedded rehydration JSON (Scout tiktok.py)."""
    m = re.search(
        r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        return None
    try:
        info = json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.user-detail"]["userInfo"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    user, stats = info.get("user") or {}, info.get("stats") or {}
    if not user:
        return None
    bio = str(user.get("signature") or "")
    bio_link = str((user.get("bioLink") or {}).get("link") or "")
    links = _dedupe(
        [bio_link if bio_link.startswith("http") else (f"https://{bio_link}" if bio_link else "")]
    )
    handle = str(user.get("uniqueId") or username)
    return Profile(
        platform="tiktok",
        username=handle,
        profile_url=profile_url("tiktok", handle),
        full_name=str(user.get("nickname") or ""),
        bio=bio,
        email=first_email(bio),
        phone=first_phone(bio),
        website=own_website(links),
        links=links,
        follower_count=parse_count(stats.get("followerCount")),
        extra={
            "verified": bool(user.get("verified")),
            "likes": parse_count(stats.get("heartCount")),
            "videos": parse_count(stats.get("videoCount")),
        },
    )


def _youtube_redirect(url: str) -> str:
    if "youtube.com/redirect" in url:
        q = re.search(r"[?&]q=([^&]+)", url)
        if q:
            return unquote(q.group(1))
    return url


def parse_youtube(html: str, identifier: str) -> Profile | None:
    """YouTube channel page: ytInitialData fragments (Scout youtube.py)."""
    name = re.search(r'"channelMetadataRenderer":\{"title":"((?:[^"\\]|\\.)*)"', html)
    if not name:
        return None
    desc = re.search(r'"channelMetadataRenderer":\{[^{}]*?"description":"((?:[^"\\]|\\.)*)"', html)
    bio = _unescape_json_string(desc.group(1)) if desc else ""
    subs = re.search(
        r'"subscriberCountText":\{(?:"simpleText"|"accessibility":\{"accessibilityData":\{"label")'
        r':"([\d.,]+[KMB]?) subscribers?"',
        html,
        re.I,
    )
    handle_m = re.search(r'"canonicalChannelUrl":"https://www\.youtube\.com/@([^"]+)"', html)
    channel_id = re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"', html)
    biz_email = re.search(r'"businessEmailLabel":\{"content":"([^"]+)"', html)
    links = _dedupe(
        [
            _youtube_redirect(_unescape_json_string(m.group(1)))
            for m in re.finditer(r'"urlEndpoint":\{"url":"(https?://[^"]+)"', html)
        ]
    )
    links = [u for u in links if "youtube.com" not in u and "google.com" not in u][:8]
    handle = handle_m.group(1) if handle_m else identifier.lstrip("@")
    return Profile(
        platform="youtube",
        username=handle,
        profile_url=profile_url("youtube", handle),
        full_name=_unescape_json_string(name.group(1)),
        bio=bio,
        email=(biz_email.group(1) if biz_email else "") or first_email(bio),
        phone=first_phone(bio),
        website=own_website(links),
        links=links,
        follower_count=parse_count(subs.group(1)) if subs else 0,
        extra={"channel_id": channel_id.group(1) if channel_id else ""},
    )


def parse_github(data: dict[str, Any]) -> Profile | None:
    """GitHub REST /users/{name} (Scout github.py). Empty profiles are skipped."""
    if not any(data.get(k) for k in ("name", "bio", "email", "blog", "company")):
        return None
    login = str(data.get("login") or "")
    bio = str(data.get("bio") or "")
    blog = str(data.get("blog") or "")
    links = _dedupe([blog if blog.startswith("http") else (f"https://{blog}" if blog else "")])
    if data.get("twitter_username"):
        links.append(f"https://x.com/{data['twitter_username']}")
    return Profile(
        platform="github",
        username=login,
        profile_url=str(data.get("html_url") or profile_url("github", login)),
        full_name=str(data.get("name") or ""),
        bio=bio,
        email=str(data.get("email") or "") or first_email(bio),
        website=own_website(links),
        links=links,
        follower_count=int(data.get("followers") or 0),
        extra={
            "company": str(data.get("company") or "").lstrip("@"),
            "location": str(data.get("location") or ""),
            "hireable": bool(data.get("hireable")),
            "public_repos": int(data.get("public_repos") or 0),
        },
        via="api",
    )


def parse_twitch(user: dict[str, Any], followers: int = 0) -> Profile | None:
    """Twitch Helix /users item (official API; Scout used the private web GQL endpoint)."""
    login = str(user.get("login") or "")
    if not login:
        return None
    bio = str(user.get("description") or "")
    links = _dedupe(re.findall(r"https?://[^\s)\"'<>]+", bio))
    return Profile(
        platform="twitch",
        username=login,
        profile_url=profile_url("twitch", login),
        full_name=str(user.get("display_name") or login),
        bio=bio,
        email=first_email(bio),
        website=own_website(links),
        links=links,
        follower_count=followers,
        extra={"broadcaster_type": str(user.get("broadcaster_type") or "")},
        via="api",
    )


def parse_instagram_apify(item: dict[str, Any], username: str) -> Profile | None:
    """apify/instagram-profile-scraper item (the licensed route; instagram.com blocks bots)."""
    handle = str(item.get("username") or username)
    if not handle:
        return None
    bio = str(item.get("biography") or "")
    links = _dedupe(
        [str(item.get("externalUrl") or "")]
        + [str(u.get("url") or "") for u in item.get("externalUrls") or [] if isinstance(u, dict)]
    )
    emails = [str(item[k]) for k in ("businessEmail", "publicEmail", "email") if item.get(k)]
    return Profile(
        platform="instagram",
        username=handle,
        profile_url=profile_url("instagram", handle),
        full_name=str(item.get("fullName") or ""),
        bio=bio,
        email=first_email(*emails, bio),
        phone=str(item.get("businessPhoneNumber") or "") or first_phone(bio),
        website=own_website(links),
        links=links,
        follower_count=int(item.get("followersCount") or 0),
        extra={"business_category": str(item.get("businessCategoryName") or "")},
        via="apify",
    )


def parse_link_hub(html: str, url: str) -> Profile | None:
    """Any link-in-bio page (Scout linktree.py: Linktree, Stan, Linkr, Bio.link + generic)."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    username = urlparse(url).path.strip("/").split("/")[0].lstrip("@")
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    full_name, bio = "", ""
    nxt = soup.find("script", id="__NEXT_DATA__")
    if nxt and nxt.string:
        try:
            account = (
                json.loads(nxt.string).get("props", {}).get("pageProps", {}).get("account") or {}
            )
        except json.JSONDecodeError:
            account = {}
        links += [str(lk.get("url")) for lk in account.get("links") or [] if lk.get("url")]
        full_name = str(account.get("pageTitle") or "")
        bio = str(account.get("description") or "")
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.startswith(("http://", "https://", "mailto:", "tel:")):
            links.append(href)
    links = [
        lk
        for lk in _dedupe(links)
        if (urlparse(lk).hostname or "").removeprefix("www.") != host
        and not any(s in lk for s in ("favicon", "/static/", "/assets/", ".css", ".js"))
    ]
    if not full_name:
        og = soup.find("meta", property="og:title")
        full_name = (
            str(og.get("content") or "") if og else (soup.title.string or "" if soup.title else "")
        )
    if not bio:
        md = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", property="og:description"
        )
        bio = str(md.get("content") or "") if md else ""
    mailto = next(
        (lk.removeprefix("mailto:").split("?")[0] for lk in links if lk.startswith("mailto:")), ""
    )
    tel = next((lk.removeprefix("tel:") for lk in links if lk.startswith("tel:")), "")
    web = [lk for lk in links if lk.startswith("http")]
    if not (full_name or web or mailto):
        return None
    return Profile(
        platform="link_hub",
        username=username,
        profile_url=url,
        full_name=full_name.strip()[:200],
        bio=bio,
        email=first_email(mailto, bio),
        phone=re.sub(r"[^\d+]", "", tel) or first_phone(bio),
        website=own_website(web),
        links=web[:20],
        extra={"host": host, "socials": socials(web)},
    )


def profile_from_search(platform: str, url: str, title: str, snippet: str) -> Profile | None:
    """Snippet-only profile for platforms whose pages forbid crawling (Pinterest, Linktree).

    Uses only what the licensed search API returned: title and description.
    """
    username = username_from_url(platform, url)
    if not username:
        return None
    if len([seg for seg in urlparse(url).path.split("/") if seg]) > 1:
        # A video/photo hit: its title and snippet describe the post, not the account.
        return Profile(
            platform=platform,
            username=username,
            profile_url=profile_url(platform, username),
            via="search",
            extra={"from_post": True},
        )
    name = re.split(r"\s+[(|•·-]\s*|\s+on\s+(?:Pinterest|Instagram|TikTok)", title, maxsplit=1)[0]
    links = _dedupe(re.findall(r"https?://[^\s)\"'<>]+", snippet))
    domains = re.findall(
        r"\b((?:[a-z0-9-]+\.)+(?:com|co\.uk|de|fr|es|it|nl|no|se|dk|fi|ie|at|ch|be|pl|pt|net|org|io))\b",
        snippet,
        re.I,
    )
    links += [f"https://{d.lower()}/" for d in domains]
    return Profile(
        platform=platform,
        username=username,
        profile_url=profile_url(platform, username),
        full_name=name.strip()[:200] or username,
        bio=snippet,
        email=first_email(snippet),
        phone=first_phone(snippet),
        website=own_website(links),
        links=_dedupe(links),
        via="search",
    )
