"""Demo source (DEMO_MODE only): realistic fixture leads on reserved example domains.

Everything it produces is tagged as mock and can never be sent to a real recipient.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.agents.discovery.base import RawBusiness, RawLead, SourceContext
from app.db.models import EvidenceKind, TriggerType

CATALOGUE: list[dict[str, Any]] = [
    {
        "t": TriggerType.EXPLICIT_REQUEST,
        "title": "Looking for a web designer for my plumbing company",
        "text": "We're a 3-person plumbing business in Austin, TX 78704. Our current site was made in "
        "2014 and doesn't work on phones. Need a redesign with online booking. Budget "
        "$1,500-2,500. Please reply with portfolio.",
        "biz": {
            "name": "Riverside Plumbing Co",
            "website": "https://riverside-plumbing.example.com",
            "address": "Austin, TX 78704",
            "country_hint": "US",
        },
    },
    {
        "t": TriggerType.EXPLICIT_REQUEST,
        "title": "Need a logo + simple website for new electrician business",
        "text": "Just set up my electrical contracting company in Manchester M4 1HN. Need a logo and a "
        "simple 4-page website so customers can find me on Google.",
        "biz": {
            "name": "Brightwire Electrical Ltd",
            "address": "Manchester M4 1HN",
            "country_hint": "GB",
        },
    },
    {
        "t": TriggerType.NO_WEBSITE,
        "title": "Oakfield Roofing — listed without a website",
        "text": "Oakfield Roofing, Leeds LS6 2AB. Listed in a trades directory without a website.",
        "biz": {
            "name": "Oakfield Roofing",
            "address": "Leeds LS6 2AB",
            "country_hint": "GB",
            "phone": "+44 113 496 0000",
            "has_website": False,
        },
    },
    {
        "t": TriggerType.BROKEN_OR_OUTDATED_SITE,
        "title": "Summit HVAC — outdated website",
        "text": "summit-hvac.example.com has no HTTPS and fails mobile usability.",
        "biz": {
            "name": "Summit HVAC Services",
            "website": "http://summit-hvac.example.com",
            "address": "Denver, CO 80203",
            "country_hint": "US",
        },
    },
    {
        "t": TriggerType.NEW_BUSINESS,
        "title": "New company: Greenleaf Landscaping Ltd",
        "text": "Greenleaf Landscaping Ltd incorporated this week (SIC 81300). Bristol BS1 4DJ.",
        "biz": {
            "name": "Greenleaf Landscaping Ltd",
            "address": "Bristol BS1 4DJ",
            "country_hint": "GB",
            "registry_number": "DEMO0001",
            "registry_source": "demo_registry",
        },
    },
    {
        "t": TriggerType.EXPLICIT_REQUEST,
        "title": "Can anyone help with SEO for my cleaning company?",
        "text": "We run a cleaning company in Tampa, FL 33602. Our website gets no traffic and we're "
        "not on the first page of Google. Looking for SEO help, ideally local SEO.",
        "biz": {
            "name": "Sparkle Clean Tampa",
            "website": "https://sparkle-clean.example.com",
            "address": "Tampa, FL 33602",
            "country_hint": "US",
        },
    },
    {
        "t": TriggerType.EXPLICIT_REQUEST,
        "title": "Wir suchen einen Webdesigner für unseren Malerbetrieb",
        "text": "Unser Malerbetrieb in Berlin braucht eine neue, moderne Webseite. Wer kann helfen?",
        "biz": {"name": "Malerbetrieb Schulz", "address": "10115 Berlin", "country_hint": "DE"},
    },
    {
        "t": TriggerType.PROFILE_WITHOUT_SITE,
        "title": "Handy Andy Repairs — profile without a site",
        "text": "Business profile for Handy Andy Repairs (Portland, OR 97205) lists no website; "
        "4.8 stars from 52 reviews.",
        "biz": {
            "name": "Handy Andy Repairs",
            "address": "Portland, OR 97205",
            "country_hint": "US",
            "has_website": False,
        },
    },
]
MAX_DEMO_LEADS = 40


class MockDemoSource:
    key: ClassVar[str] = "mock_demo"
    label: ClassVar[str] = "Demo data (DEMO_MODE)"
    description: ClassVar[str] = "Fixture leads for development and demos. Never sendable."
    integration: ClassVar[str] = "web"
    default_enabled: ClassVar[bool] = False
    default_schedule_minutes: ClassVar[int] = 15
    constraints: ClassVar[str] = "Only runs with DEMO_MODE=true; uses reserved example domains."
    default_config: ClassVar[dict[str, Any]] = {"per_run": 3}

    async def fetch(self, ctx: SourceContext) -> list[RawLead]:
        counter = int(ctx.state.get("counter", 0))
        if counter >= MAX_DEMO_LEADS:
            return []
        per_run = int(ctx.config.get("per_run", 3))
        leads = []
        for i in range(counter, min(counter + per_run, MAX_DEMO_LEADS)):
            item = CATALOGUE[i % len(CATALOGUE)]
            round_no = i // len(CATALOGUE)
            biz = dict(item["biz"])
            if round_no:
                biz["name"] = f"{biz['name']} {round_no + 1}"
                if biz.get("website"):
                    biz["website"] = biz["website"].replace(
                        ".example.com", f"-{round_no + 1}.example.com"
                    )
                if biz.get("registry_number"):
                    biz["registry_number"] = f"{biz['registry_number']}-{round_no + 1}"
            leads.append(
                RawLead(
                    source_item_id=f"demo:{i}",
                    source_url=f"https://forum.example.com/thread/{1000 + i}",
                    trigger_type=item["t"],
                    title=item["title"],
                    text=item["text"],
                    author=f"demo_user_{i}",
                    business=RawBusiness(**biz),
                    evidence_kind=EvidenceKind.POST,
                    evidence_data={"demo": True},
                )
            )
        ctx.state["counter"] = counter + len(leads)
        return leads
