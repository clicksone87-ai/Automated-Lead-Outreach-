"""Freelancer.com API and alert-email sources against fixtures (no live calls)."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters.mailbox import MockMailbox, parse_message
from app.adapters.registry import build_adapters
from app.agents.discovery.base import SourceContext
from app.agents.discovery.sources.boards import AlertEmailSource, FreelancerSource
from app.config import Settings
from app.db.models import TriggerType
from app.db.session import session_scope
from app.services import runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _mail(uid: str, sender: str, subject: str, body: str) -> Any:
    msg = EmailMessage()
    msg["From"] = f"Alerts <{sender}>"
    msg["To"] = "alerts@studio.test"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{uid}@mail.test>"
    msg["Date"] = "Mon, 16 Nov 2026 09:00:00 +0000"
    msg.set_content(body)
    return parse_message(uid, msg.as_bytes())


async def _ctx(s: Any, config: dict[str, Any], mailbox: MockMailbox | None = None) -> SourceContext:
    settings = Settings(_env_file=None, demo_mode=True, data_dir=Path("./.test-data"))  # type: ignore[call-arg]
    adapters = build_adapters(settings)
    if mailbox is not None:
        adapters.mailbox = mailbox
    return SourceContext(
        session=s,
        adapters=adapters,
        settings=settings,
        runtime=await runtime_settings.load(s),
        config=config,
    )


def test_parse_message_extracts_text_and_links() -> None:
    mail = _mail(
        "7",
        "noreply@upwork.com",
        "New job: Website for HVAC company",
        "Client needs a new website.\nView job: https://www.upwork.com/jobs/~01abc\n",
    )
    assert mail.from_email == "noreply@upwork.com"
    assert mail.links == ["https://www.upwork.com/jobs/~01abc"]
    assert mail.message_id == "<7@mail.test>"


async def test_alert_emails_become_leads_and_are_marked_seen() -> None:
    box = MockMailbox(
        {
            "Alerts": [
                _mail(
                    "1",
                    "noreply@upwork.com",
                    "New job: Logo and website for roofing company",
                    "A roofing company needs a logo and a 5-page website. https://www.upwork.com/jobs/~01x",
                ),
                _mail("2", "news@upwork.com", "Upwork product updates", "We changed our fees."),
                _mail(
                    "3",
                    "friend@gmail.com",
                    "need a website?",
                    "Hey, do you need a website for your club?",
                ),
                _mail(
                    "4",
                    "alerts@peopleperhour.com",
                    "Job: SEO for local plumber",
                    "Local SEO needed for a plumbing company in Leeds.",
                ),
            ]
        }
    )
    async with session_scope() as s:
        leads = await AlertEmailSource().fetch(await _ctx(s, {"max_per_run": 50}, box))
    assert [lead.evidence_data["platform"] for lead in leads] == ["upwork", "peopleperhour"]
    assert leads[0].source_url == "https://www.upwork.com/jobs/~01x"  # stored, never fetched
    assert leads[1].source_url.startswith("mid:")
    assert all(lead.trigger_type == TriggerType.EXPLICIT_REQUEST for lead in leads)
    assert box.seen == {"1", "2", "3", "4"}


@respx.mock
async def test_freelancer_api_fixture() -> None:
    respx.get(FreelancerSource.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "projects": [
                        {
                            "id": 101,
                            "title": "Need a new website for my cleaning business",
                            "seo_url": "web/clean-site",
                            "description": "Looking for a website with booking.",
                            "time_submitted": 1794000000,
                            "budget": {"minimum": 250, "maximum": 750},
                            "currency": {"code": "USD"},
                        },
                        {"id": 102, "title": "Data entry", "description": "Copy rows into Excel"},
                    ]
                }
            },
        )
    )
    async with session_scope() as s:
        leads = await FreelancerSource().fetch(
            await _ctx(s, {"queries": ["website design"], "limit": 30})
        )
    assert [lead.source_item_id for lead in leads] == ["fl:101"]
    assert leads[0].source_url == "https://www.freelancer.com/projects/web/clean-site"
    assert leads[0].evidence_data["budget"] == {"minimum": 250, "maximum": 750}
