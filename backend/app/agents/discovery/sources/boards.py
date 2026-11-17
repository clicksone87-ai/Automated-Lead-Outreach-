"""Freelance boards and classifieds, through official APIs and alert emails only.

The platforms are never logged in to or scraped; links in alert emails are stored, not fetched.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx

from app.agents.discovery.base import RawLead, SourceContext, detect_services
from app.db.models import EvidenceKind, TriggerType

logger = logging.getLogger(__name__)

ALERT_SENDERS = {
    "upwork.com": "upwork",
    "fiverr.com": "fiverr",
    "peopleperhour.com": "peopleperhour",
    "freelancer.com": "freelancer",
    "gumtree.com": "gumtree",
    "craigslist.org": "craigslist",
    "guru.com": "guru",
}


class FreelancerSource:
    key: ClassVar[str] = "freelancer"
    label: ClassVar[str] = "Freelancer.com (official API)"
    description: ClassVar[str] = "Active projects asking for logos, websites, redesigns or SEO."
    integration: ClassVar[str] = "web"  # public endpoint; FREELANCER_API_TOKEN is optional
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 30
    constraints: ClassVar[str] = "Official API only; replies happen on the platform by a human."
    default_config: ClassVar[dict[str, Any]] = {
        "queries": ["website design", "logo design", "website redesign", "local seo"],
        "limit": 30,
    }
    ENDPOINT = "https://www.freelancer.com/api/projects/0.1/projects/active/"

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        headers = {}
        if ctx.settings.freelancer_api_token:
            headers["freelancer-oauth-v1"] = ctx.settings.freelancer_api_token.get_secret_value()
        leads: list[RawLead] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for query in ctx.config.get("queries", []):
                resp = await client.get(
                    self.ENDPOINT,
                    headers=headers,
                    params={
                        "query": query,
                        "limit": str(ctx.config.get("limit", 30)),
                        "full_description": "true",
                        "job_details": "true",
                    },
                )
                resp.raise_for_status()
                for p in resp.json().get("result", {}).get("projects", []):
                    text = f"{p.get('title', '')}\n{p.get('description') or p.get('preview_description', '')}"
                    if not detect_services(text):
                        continue
                    budget = p.get("budget") or {}
                    leads.append(
                        RawLead(
                            source_item_id=f"fl:{p['id']}",
                            source_url=f"https://www.freelancer.com/projects/{p.get('seo_url') or p['id']}",
                            trigger_type=TriggerType.EXPLICIT_REQUEST,
                            title=str(p.get("title", ""))[:500],
                            text=text[:8000],
                            posted_at=datetime.fromtimestamp(p["time_submitted"], tz=UTC)
                            if p.get("time_submitted")
                            else None,
                            evidence_kind=EvidenceKind.POST,
                            evidence_data={
                                "platform": "freelancer",
                                "budget": budget,
                                "currency": (p.get("currency") or {}).get("code"),
                            },
                        )
                    )
        return leads


class AlertEmailSource:
    key: ClassVar[str] = "alert_emails"
    label: ClassVar[str] = "Job-board & classifieds alert emails"
    description: ClassVar[str] = (
        "Reads alert emails you subscribe to (Upwork, Fiverr, PeoplePerHour, Gumtree, …) from "
        "the mailbox folder set in ALERTS_IMAP_FOLDER."
    )
    integration: ClassVar[str] = "mailbox"
    default_enabled: ClassVar[bool] = True
    default_schedule_minutes: ClassVar[int] = 20
    constraints: ClassVar[str] = (
        "Alerts only: platforms are never logged into or scraped; links are references, never "
        "fetched."
    )
    default_config: ClassVar[dict[str, Any]] = {"max_per_run": 50}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        folder = ctx.settings.alerts_imap_folder
        mails = await ctx.adapters.mailbox.fetch_unseen(
            folder, int(ctx.config.get("max_per_run", 50))
        )
        leads: list[RawLead] = []
        for mail in mails:
            domain = mail.from_email.rsplit("@", 1)[-1]
            platform = next(
                (p for d, p in ALERT_SENDERS.items() if domain == d or domain.endswith(f".{d}")),
                None,
            )
            text = f"{mail.subject}\n{mail.text}"
            # Alerts are requests by nature; keep those that ask for one of our services.
            if platform is None or not detect_services(text):
                continue
            platform_links = [u for u in mail.links if any(d in u for d in ALERT_SENDERS)]
            leads.append(
                RawLead(
                    source_item_id="mail:"
                    + hashlib.sha256(mail.message_id.encode()).hexdigest()[:32],
                    source_url=platform_links[0] if platform_links else f"mid:{mail.message_id}",
                    trigger_type=TriggerType.EXPLICIT_REQUEST,
                    title=mail.subject[:500],
                    text=mail.text[:8000],
                    posted_at=mail.received_at,
                    evidence_kind=EvidenceKind.POST,
                    evidence_data={
                        "platform": platform,
                        "via": "alert_email",
                        "from": mail.from_email,
                        "links": platform_links[:5],
                    },
                )
            )
        await ctx.adapters.mailbox.mark_seen(folder, [m.uid for m in mails])
        return leads
