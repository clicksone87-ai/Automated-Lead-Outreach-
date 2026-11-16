"""Structured outputs for the audit (vision critique) and the stored report."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]


class DesignIssue(BaseModel):
    area: Literal[
        "layout", "typography", "colour", "imagery", "navigation", "cta", "copy", "mobile", "trust"
    ]
    severity: Severity
    observation: str = Field(max_length=300, description="What is visible in the screenshot.")


class DesignCritique(BaseModel):
    """Vision model critique of the desktop + mobile screenshots."""

    design_score: int = Field(ge=0, le=100)
    looks_outdated: bool
    mobile_friendly: bool
    cta_visible_above_fold: bool
    strengths: list[str] = Field(default_factory=list, max_length=5)
    issues: list[DesignIssue] = Field(default_factory=list, max_length=8)

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        return cls(
            design_score=48,
            looks_outdated=True,
            mobile_friendly=False,
            cta_visible_above_fold=False,
            strengths=["Clear business name in the header"],
            issues=[
                DesignIssue(
                    area="mobile",
                    severity="high",
                    observation="Text is very small on the phone screenshot.",
                ),
                DesignIssue(
                    area="cta",
                    severity="medium",
                    observation="No visible call-to-action button above the fold.",
                ),
            ],
        )


class Finding(BaseModel):
    id: str
    category: str
    severity: Severity
    title: str
    detail: str
    evidence_id: str  # REQUIRED: every finding links to stored evidence
    metric: dict[str, Any] = Field(default_factory=dict)
    check: str = ""


class Competitor(BaseModel):
    domain: str
    url: str
    https: bool
    mobile_ready: bool
    has_structured_data: bool
    has_cta: bool
    title: str | None
    evidence_id: str
