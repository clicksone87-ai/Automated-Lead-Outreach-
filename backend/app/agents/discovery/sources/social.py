"""X (official API, paid, off by default, budget-capped) and Meta Graph API (public page
content accessible to the app only; no private groups/profiles)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, ClassVar

import httpx

from app.agents.discovery.base import (
    RawLead,
    SourceContext,
    industry_keywords,
    looks_like_request,
)
from app.db.models import EvidenceKind, TriggerType
from app.orchestrator import budget


class XSource:
    key: ClassVar[str] = "x"
    label: ClassVar[str] = "X (official API)"
    description: ClassVar[str] = "Recent posts asking for a website, logo or SEO help."
    integration: ClassVar[str] = "x"
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 60
    constraints: ClassVar[str] = "Paid, usage-priced API; off by default; budget-capped."
    default_config: ClassVar[dict[str, Any]] = {"max_results": 25}
    ENDPOINT = "https://api.x.com/2/tweets/search/recent"

    def query(self, ctx: SourceContext) -> str:
        kws = " OR ".join(industry_keywords(ctx.runtime.active_industry)[:6])
        return (
            f'("need a website" OR "looking for a web designer" OR "need a logo" OR '
            f'"website redesign") ({kws}) -is:retweet lang:en'
        )

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        token = ctx.settings.x_bearer_token
        if token is None:
            return []
        max_results = int(ctx.config.get("max_results", 25))
        price = ctx.runtime.provider_prices.get("x", 0.01)
        await budget.charge(
            ctx.session,
            provider="x",
            agent="discovery",
            amount_usd=price * max_results,
            description="x recent search",
        )
        params: dict[str, Any] = {
            "query": self.query(ctx),
            "max_results": max(10, min(max_results, 100)),
            "tweet.fields": "created_at,author_id,lang",
        }
        if ctx.state.get("since_id"):
            params["since_id"] = ctx.state["since_id"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self.ENDPOINT,
                params=params,
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("meta", {}).get("newest_id"):
            ctx.state["since_id"] = body["meta"]["newest_id"]
        leads = []
        for tweet in body.get("data", []):
            text = tweet.get("text", "")
            if not looks_like_request(text):
                continue
            leads.append(
                RawLead(
                    source_item_id=f"x:{tweet['id']}",
                    source_url=f"https://x.com/i/web/status/{tweet['id']}",
                    trigger_type=TriggerType.EXPLICIT_REQUEST,
                    title=text[:140],
                    text=text,
                    author=tweet.get("author_id"),
                    posted_at=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
                    if tweet.get("created_at")
                    else None,
                    evidence_kind=EvidenceKind.POST,
                    evidence_data={"platform": "x", "lang": tweet.get("lang")},
                )
            )
        return leads


class MetaSource:
    key: ClassVar[str] = "meta"
    label: ClassVar[str] = "Facebook / Instagram (Graph API)"
    description: ClassVar[str] = "Posts on configured public business pages the app can access."
    integration: ClassVar[str] = "meta"
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 60
    constraints: ClassVar[str] = (
        "Graph API only, after app review; only accessible public business content; never "
        "private groups or profiles."
    )
    default_config: ClassVar[dict[str, Any]] = {"page_ids": [], "graph_version": "v21.0"}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        token = ctx.settings.meta_access_token
        if token is None:
            return []
        version = ctx.config.get("graph_version", "v21.0")
        leads: list[RawLead] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page_id in ctx.config.get("page_ids", []):
                resp = await client.get(
                    f"https://graph.facebook.com/{version}/{page_id}/posts",
                    params={
                        "fields": "message,permalink_url,created_time",
                        "limit": "50",
                        "access_token": token.get_secret_value(),
                    },
                )
                resp.raise_for_status()
                for post in resp.json().get("data", []):
                    msg = post.get("message", "")
                    if not looks_like_request(msg):
                        continue
                    leads.append(
                        RawLead(
                            source_item_id="meta:"
                            + hashlib.sha256(post["id"].encode()).hexdigest()[:32],
                            source_url=post.get("permalink_url")
                            or f"https://facebook.com/{post['id']}",
                            trigger_type=TriggerType.EXPLICIT_REQUEST,
                            title=msg[:140],
                            text=msg[:8000],
                            posted_at=datetime.fromisoformat(
                                post["created_time"].replace("+0000", "+00:00")
                            )
                            if post.get("created_time")
                            else None,
                            evidence_kind=EvidenceKind.POST,
                            evidence_data={"platform": "facebook", "page_id": page_id},
                        )
                    )
        return leads
