"""Discovery source contract.

Each source is its own module, can be enabled/disabled in settings, has its own schedule and
declares the integration it depends on (so a missing key shows "Needs API key"). Sources only
*find* raw items; the runner handles dedup, locale, evidence and lead creation uniformly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters
from app.config import Settings
from app.db.models import EvidenceKind, TriggerType
from app.services.runtime_settings import RuntimeSettings


class RawBusiness(BaseModel):
    name: str
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    country_hint: str | None = None
    registry_number: str | None = None
    registry_source: str | None = None
    industry: str | None = None
    has_website: bool | None = None


class RawLead(BaseModel):
    source_item_id: str = Field(max_length=300)
    source_url: str
    trigger_type: TriggerType
    title: str | None = None
    text: str | None = None
    author: str | None = None
    posted_at: datetime | None = None
    business: RawBusiness | None = None
    evidence_kind: EvidenceKind = EvidenceKind.POST
    evidence_data: dict[str, Any] = Field(default_factory=dict)
    language_hint: str | None = None


@dataclass(slots=True)
class SourceContext:
    session: AsyncSession
    adapters: Adapters
    settings: Settings
    runtime: RuntimeSettings
    config: dict[str, Any]
    #: mutable per-source state persisted in `sources.config["_state"]` (cursors, rotation)
    state: dict[str, Any] = field(default_factory=dict)


class DiscoverySource(Protocol):
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    #: integration key from config.integration_status()
    integration: ClassVar[str]
    default_enabled: ClassVar[bool]
    default_schedule_minutes: ClassVar[int]
    #: platform constraints enforced in code, shown on the Sources page
    constraints: ClassVar[str]
    default_config: ClassVar[dict[str, Any]]

    async def fetch(self, ctx: SourceContext) -> list[RawLead]: ...


# ── shared helpers for sources ─────────────────────────────────────────────

SERVICE_PATTERNS = {
    "website": r"\b(new|build|create|need|looking for)\b.{0,40}\b(web ?site|webpage|web page|"
    r"landing page|online presence)\b|\bweb ?(designer|developer)\b",
    "redesign": r"\b(redesign|revamp|refresh|modernise|modernize|update|rebuild)\b.{0,30}"
    r"\b(web ?site|site)\b|\boutdated (web ?site|site)\b",
    "logo": r"\b(logo|branding|brand identity|brand design)\b",
    "seo": r"\b(seo|search engine optimi[sz]ation|rank(ing)? (higher|on google)|google ranking|"
    r"local seo)\b",
}
_SERVICE_RE = {k: re.compile(v, re.I) for k, v in SERVICE_PATTERNS.items()}
REQUEST_RE = re.compile(
    r"\b(hiring|looking for|need(ed)?|seeking|recommend|anyone (know|build)|quote|budget|"
    r"\[hiring\]|\[task\]|help with)\b",
    re.I,
)


def detect_services(text: str) -> list[str]:
    return [svc for svc, rx in _SERVICE_RE.items() if rx.search(text)]


def looks_like_request(text: str) -> bool:
    """Cheap pre-filter so obviously irrelevant items never reach the (paid) Requirements step."""
    return bool(detect_services(text)) and bool(REQUEST_RE.search(text))


INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "home_services": [
        "plumber",
        "plumbing",
        "electrician",
        "electrical contractor",
        "roofer",
        "roofing",
        "handyman",
        "hvac",
        "heating",
        "carpenter",
        "joiner",
        "painter decorator",
        "landscaper",
        "landscaping",
        "cleaning company",
        "cleaner",
        "locksmith",
        "builder",
        "gardener",
        "pest control",
        "flooring",
        "tiler",
        "plasterer",
    ],
}


def industry_keywords(industry: str) -> list[str]:
    return INDUSTRY_KEYWORDS.get(industry, [industry.replace("_", " ")])
