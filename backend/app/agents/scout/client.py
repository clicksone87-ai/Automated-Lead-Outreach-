"""Fetches creator/business profiles for Scout, inside the system's platform rules.

Per platform:
- TikTok, YouTube: the public profile page through the safe fetcher (robots.txt allows it).
- GitHub: the official REST API (optional GITHUB_TOKEN raises the 60/hour limit).
- Twitch: the official Helix API (TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET); without keys the
  search snippet is used.
- Instagram: the licensed Apify profile actor (instagram.com forbids crawling).
- Pinterest, Linktree: search snippet only — both forbid crawling in robots.txt.
- Other link-in-bio hosts (bio.link, lnk.bio, beacons.ai, ...): page via the safe fetcher,
  which skips any host whose robots.txt says no.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.apify import ApifyRunError
from app.adapters.fetcher import FetchError
from app.adapters.registry import Adapters
from app.adapters.search import SearchResult
from app.agents.scout import parsers
from app.agents.scout.parsers import Profile
from app.config import Settings
from app.orchestrator.budget import BudgetExceededError

logger = logging.getLogger(__name__)

INSTAGRAM_PROFILE_ACTOR = "apify/instagram-profile-scraper"
SNIPPET_ONLY = {"pinterest", "linktree"}
_twitch_token: tuple[str, float] | None = None  # (token, expires_at) — process-wide cache


def _secret(value: Any) -> str:
    if value is None:
        return ""
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return raw.strip()


class ScoutClient:
    def __init__(self, session: AsyncSession, adapters: Adapters, settings: Settings) -> None:
        self.session, self.adapters, self.settings = session, adapters, settings

    async def profile(
        self, platform: str, username: str, *, hint: SearchResult | None = None
    ) -> Profile | None:
        """One profile, or None. Falls back to the search snippet when the page can't be read."""
        fetched: Profile | None = None
        try:
            if platform == "tiktok":
                fetched = await self._page(platform, username, parsers.parse_tiktok)
            elif platform == "youtube":
                fetched = await self._page(platform, username, parsers.parse_youtube)
            elif platform == "github":
                fetched = await self._github(username)
            elif platform == "twitch":
                fetched = await self._twitch(username)
            elif platform == "instagram":
                fetched = await self._instagram(username)
        except (FetchError, httpx.HTTPError, ValueError) as exc:
            logger.info("scout %s/%s not read directly: %s", platform, username, exc)
        if fetched is None and hint is not None:
            fetched = parsers.profile_from_search(platform, hint.url, hint.title, hint.description)
        return fetched

    async def link_hub(self, url: str) -> Profile | None:
        """A link-in-bio page; None when its robots.txt forbids crawling or it can't be read."""
        try:
            page = await self.adapters.fetcher.get(url)
        except FetchError as exc:
            logger.info("link hub %s skipped: %s", url, exc)
            return None
        if not page.ok:
            return None
        return parsers.parse_link_hub(page.text, page.final_url)

    # ── per platform ─────────────────────────────────────────────────────

    async def _page(self, platform: str, username: str, parse: Any) -> Profile | None:
        page = await self.adapters.fetcher.get(
            parsers.profile_url(platform, username), headers={"Accept-Language": "en"}
        )
        if not page.ok:
            return None
        result: Profile | None = parse(page.text, username)
        return result

    async def _github(self, username: str) -> Profile | None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = _secret(self.settings.github_token)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # An official JSON API, not a crawl: robots.txt does not apply.
        page = await self.adapters.fetcher.get(
            f"https://api.github.com/users/{username}", respect_robots=False, headers=headers
        )
        if not page.ok:
            return None
        return parsers.parse_github(json.loads(page.text))

    async def _twitch(self, username: str) -> Profile | None:
        client_id = _secret(self.settings.twitch_client_id)
        token = await self._twitch_token()
        if not token:
            return None
        headers = {"Client-Id": client_id, "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                "https://api.twitch.tv/helix/users", params={"login": username}, headers=headers
            )
            r.raise_for_status()
            users = r.json().get("data") or []
            if not users:
                return None
            f = await http.get(
                "https://api.twitch.tv/helix/channels/followers",
                params={"broadcaster_id": users[0]["id"], "first": 1},
                headers=headers,
            )
            followers = int(f.json().get("total") or 0) if f.status_code == 200 else 0
        return parsers.parse_twitch(users[0], followers)

    async def _twitch_token(self) -> str | None:
        global _twitch_token  # noqa: PLW0603 - app access tokens last ~60 days; cache per process
        client_id = _secret(self.settings.twitch_client_id)
        secret = _secret(self.settings.twitch_client_secret)
        if not (client_id and secret):
            return None
        if _twitch_token and _twitch_token[1] > time.time() + 60:
            return _twitch_token[0]
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": client_id,
                    "client_secret": secret,
                    "grant_type": "client_credentials",
                },
            )
            r.raise_for_status()
            body = r.json()
        _twitch_token = (str(body["access_token"]), time.time() + int(body.get("expires_in", 3600)))
        return _twitch_token[0]

    async def _instagram(self, username: str) -> Profile | None:
        apify = self.adapters.apify
        if apify is None:
            return None
        try:
            run = await apify.run_actor(
                self.session,
                INSTAGRAM_PROFILE_ACTOR,
                {"usernames": [username]},
                source="social_instagram",  # shares the Instagram budget slice
                max_items=1,
                max_charge_usd=0.01,
            )
        except (ApifyRunError, BudgetExceededError) as exc:
            logger.info("instagram profile %s skipped: %s", username, exc)
            return None
        return parsers.parse_instagram_apify(run.items[0], username) if run.items else None
