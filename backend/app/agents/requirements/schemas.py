"""Structured outputs for the Requirements agent (validated by Pydantic; the specification §5.7)."""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator

from app.agents.discovery.base import detect_services

ServiceName = Literal["logo", "website", "redesign", "seo"]
RecipientType = Literal["corporate_entity", "sole_trader", "individual", "unknown"]

_BUDGET_RE = re.compile(
    r"(?:budget|pay|paying|up to|around|max)\D{0,15}([$£€]\s?\d[\d,.]*k?|\d[\d,.]*\s?(?:usd|eur|gbp|dollars|pounds|euros))",
    re.I,
)
_TIMELINE_RE = re.compile(
    r"\b(asap|urgent(ly)?|this week|next week|by (monday|friday|the end of \w+)|within \d+ (days|weeks))\b",
    re.I,
)
_LTD_RE = re.compile(r"\b(ltd|limited|llc|inc|gmbh|plc|corp|s\.?l\.?|sarl)\b", re.I)


class RequirementsLLM(BaseModel):
    """What the model extracts from an explicit request. Facts must come from the text."""

    is_buying_intent: bool = Field(
        description="True only if the author wants to hire/buy a logo, website, redesign or SEO "
        "for their own business now. False for general discussion, advice-seeking, or sellers."
    )
    intent_score: int = Field(ge=0, le=100, description="Likelihood of buying soon, 0-100.")
    intent_reasoning: str = Field(max_length=400)
    services: list[ServiceName] = Field(default_factory=list)
    business_name: str | None = Field(default=None, description="Only if stated in the text.")
    business_type: str | None = Field(default=None, description="e.g. plumber, cleaning company")
    location: str | None = Field(default=None, description="Location exactly as stated, else null")
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2, only if clear")
    language: str | None = Field(default=None, description="ISO 639-1 of the text")
    existing_website: str | None = Field(default=None, description="URL only if in the text")
    budget: str | None = Field(default=None, description="As stated, else null")
    timeline: str | None = Field(default=None, description="As stated, else null")
    recipient_type: RecipientType = "unknown"
    available_info: list[str] = Field(default_factory=list, max_length=12)
    evidence_quotes: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Short exact quotes from the text that show the need/intent.",
    )

    @field_validator("country")
    @classmethod
    def _iso2(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper().replace("UK", "GB")
        return v if len(v) == 2 and v.isalpha() else None

    @field_validator("language")
    @classmethod
    def _iso639(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()[:2]
        return v if v.isalpha() else None

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        """Deterministic stand-in used in tests/DEMO_MODE — derived only from the text."""
        text = "\n".join(context.get("untrusted", {}).values())
        sentences = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]
        quotes = [
            s for s in sentences if re.search(r"\b(need|looking|want|hiring|help)\b", s, re.I)
        ]
        services = detect_services(text)
        budget = _BUDGET_RE.search(text)
        timeline = _TIMELINE_RE.search(text)
        intent = bool(quotes and services)
        score = (55 if intent else 15) + (15 if budget else 0) + (5 if timeline else 0)
        name = context.get("business_name")
        return cls(
            is_buying_intent=intent,
            intent_score=min(score, 95),
            intent_reasoning="mock: request language + service keywords"
            if intent
            else "mock: no clear request",
            services=services,
            business_name=name,
            business_type=None,
            location=None,
            country=None,
            language=None,
            existing_website=None,
            budget=budget.group(0) if budget else None,
            timeline=timeline.group(0) if timeline else None,
            recipient_type="corporate_entity" if name and _LTD_RE.search(name) else "unknown",
            available_info=[],
            evidence_quotes=quotes[:3],
        )


class IntentBreakdown(BaseModel):
    llm_score: int | None
    rule_score: int
    combined: int
    signals: dict[str, int]


class Brief(BaseModel):
    """Stored in `briefs.data`; read by every downstream agent."""

    services: list[ServiceName]
    business_name: str | None
    business_type: str | None
    location: str | None
    country: str | None
    language: str | None
    timezone: str | None
    existing_website: str | None
    recipient_type: RecipientType
    budget: str | None
    timeline: str | None
    available_info: list[str]
    evidence_quotes: list[str]
    intent: IntentBreakdown
    agents_to_run: list[str]
    assumptions: list[str] = Field(default_factory=list)
    used_llm: bool
