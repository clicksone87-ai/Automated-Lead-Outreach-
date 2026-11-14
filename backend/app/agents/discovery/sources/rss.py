"""RSS / Atom source (public feeds only, fetched through the safe fetcher)."""

from __future__ import annotations

import calendar
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

import feedparser
from bs4 import BeautifulSoup

from app.adapters.fetcher import FetchError
from app.agents.discovery.base import RawLead, SourceContext, looks_like_request
from app.db.models import EvidenceKind, TriggerType

logger = logging.getLogger(__name__)


class RssSource:
    key: ClassVar[str] = "rss"
    label: ClassVar[str] = "RSS / Atom feeds"
    description: ClassVar[str] = (
        "Public RSS/Atom feeds (forums, job boards, communities). Configure feed URLs in "
        "Sources → RSS."
    )
    integration: ClassVar[str] = "rss"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 20
    constraints: ClassVar[str] = "Public feeds only; robots.txt and rate limits respected."
    default_config: ClassVar[dict[str, Any]] = {
        # Each feed: {"url": "...", "label": "...", "country": "US"|null}
        "feeds": [],
        "max_items_per_feed": 50,
    }

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        leads: list[RawLead] = []
        limit = int(ctx.config.get("max_items_per_feed", 50))
        for feed_cfg in ctx.config.get("feeds", []):
            url = feed_cfg.get("url") if isinstance(feed_cfg, dict) else str(feed_cfg)
            if not url:
                continue
            try:
                resp = await ctx.adapters.fetcher.get(url)
            except FetchError as exc:
                logger.info("feed %s skipped: %s", url, exc)
                continue
            if not resp.ok:
                continue
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:limit]:
                leads.extend(self._entry(entry, url, feed_cfg))
        return leads

    @staticmethod
    def _entry(entry: Any, feed_url: str, feed_cfg: Any) -> list[RawLead]:
        title = str(entry.get("title", ""))
        summary_html = str(entry.get("summary", "") or entry.get("description", ""))
        summary = BeautifulSoup(summary_html, "lxml").get_text(" ", strip=True)
        text = f"{title}\n{summary}"
        if not looks_like_request(text):
            return []
        link = str(entry.get("link") or feed_url)
        item_id = str(entry.get("id") or entry.get("guid") or link)
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        posted_at = (
            datetime.fromtimestamp(calendar.timegm(published), tz=UTC) if published else None
        )
        country = feed_cfg.get("country") if isinstance(feed_cfg, dict) else None
        return [
            RawLead(
                source_item_id=hashlib.sha256(item_id.encode()).hexdigest()[:40],
                source_url=link,
                trigger_type=TriggerType.EXPLICIT_REQUEST,
                title=title[:500],
                text=summary[:8000],
                author=str(entry.get("author")) if entry.get("author") else None,
                posted_at=posted_at,
                evidence_kind=EvidenceKind.POST,
                evidence_data={"feed": feed_url, "country_hint": country},
            )
        ]
