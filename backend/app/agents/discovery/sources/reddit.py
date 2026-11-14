"""Reddit via the official Data API (OAuth). Needs approved/commercial access.

Rules enforced: read-only (we never send automated messages on Reddit); content that is
deleted/removed on Reddit is deleted from our DB by `sync_deletions` (scheduled).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.discovery.base import RawLead, SourceContext, looks_like_request
from app.config import Settings
from app.db.models import Evidence, EvidenceKind, Lead, LeadStatus, TriggerType

logger = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"  # noqa: S105 - URL, not a secret
API = "https://oauth.reddit.com"
# Leads already contacted keep a minimal record (no post content) for suppression/cooldown.
CONTACTED_STATES = {LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.WON, LeadStatus.LOST}


async def _token(client: httpx.AsyncClient, s: Settings) -> str:
    assert s.reddit_client_secret is not None
    resp = await client.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(s.reddit_client_id, s.reddit_client_secret.get_secret_value()),
        headers={"User-Agent": s.reddit_user_agent},
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


class RedditSource:
    key: ClassVar[str] = "reddit"
    label: ClassVar[str] = "Reddit (Data API)"
    description: ClassVar[str] = "New posts in configured subreddits that ask for our services."
    integration: ClassVar[str] = "reddit"
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 30
    constraints: ClassVar[str] = (
        "Official API with approved access only; read-only, no automated messages; deleted "
        "content is removed from our database."
    )
    default_config: ClassVar[dict[str, Any]] = {
        "subreddits": ["forhire", "smallbusiness", "Entrepreneur"],
        "limit": 100,
    }

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        s = ctx.settings
        leads: list[RawLead] = []
        async with httpx.AsyncClient(timeout=30) as client:
            token = await _token(client, s)
            headers = {"Authorization": f"bearer {token}", "User-Agent": s.reddit_user_agent}
            for sub in ctx.config.get("subreddits", []):
                resp = await client.get(
                    f"{API}/r/{sub}/new",
                    params={"limit": ctx.config.get("limit", 100)},
                    headers=headers,
                )
                resp.raise_for_status()
                for child in resp.json().get("data", {}).get("children", []):
                    post = child.get("data", {})
                    if post.get("removed_by_category") or post.get("author") == "[deleted]":
                        continue
                    text = f"{post.get('title', '')}\n{post.get('selftext', '')}"
                    if not looks_like_request(text):
                        continue
                    leads.append(
                        RawLead(
                            source_item_id=f"t3_{post['id']}",
                            source_url=f"https://www.reddit.com{post.get('permalink', '')}",
                            trigger_type=TriggerType.EXPLICIT_REQUEST,
                            title=str(post.get("title", ""))[:500],
                            text=str(post.get("selftext", ""))[:8000],
                            author=post.get("author"),
                            posted_at=datetime.fromtimestamp(post.get("created_utc", 0), tz=UTC),
                            evidence_kind=EvidenceKind.POST,
                            evidence_data={"subreddit": sub, "fullname": f"t3_{post['id']}"},
                        )
                    )
        return leads


async def sync_deletions(session: AsyncSession, s: Settings, days: int = 30) -> int:
    """Remove content of Reddit posts that were deleted/removed on Reddit. Returns count."""
    since = datetime.now(UTC) - timedelta(days=days)
    leads = (
        (
            await session.execute(
                select(Lead).where(
                    Lead.source == "reddit", Lead.created_at >= since, Lead.raw_text.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if not leads:
        return 0
    removed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        token = await _token(client, s)
        headers = {"Authorization": f"bearer {token}", "User-Agent": s.reddit_user_agent}
        for i in range(0, len(leads), 100):
            chunk = leads[i : i + 100]
            resp = await client.get(
                f"{API}/api/info",
                params={"id": ",".join(lead.source_item_id for lead in chunk)},
                headers=headers,
            )
            resp.raise_for_status()
            status = {
                c["data"]["name"]: c["data"]
                for c in resp.json().get("data", {}).get("children", [])
            }
            for lead in chunk:
                post = status.get(lead.source_item_id)
                gone = (
                    post is None
                    or post.get("removed_by_category")
                    or post.get("author") == "[deleted]"
                    or post.get("selftext") in ("[removed]", "[deleted]")
                )
                if not gone:
                    continue
                removed += 1
                await session.execute(delete(Evidence).where(Evidence.lead_id == lead.id))
                if lead.status in CONTACTED_STATES:
                    lead.raw_text, lead.title = None, "[deleted on Reddit]"
                else:
                    await session.delete(lead)
    await session.flush()
    logger.info("reddit deletion sync removed %d item(s)", removed)
    return removed
