"""Agent contract and registry.

Agents read inputs from the database, write structured results back, and must be idempotent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters
from app.config import Settings


@dataclass(slots=True)
class AgentContext:
    session: AsyncSession
    adapters: Adapters
    settings: Settings


@dataclass(slots=True)
class AgentOutcome:
    proceed: bool
    status: str | None = None  # new lead status, if it changed
    reason: str = ""
    output: dict[str, Any] = field(default_factory=dict)
    paid: bool = False


class LeadAgent(Protocol):
    name: ClassVar[str]
    #: True if this agent spends money (gated by qualification + verified contact route)
    expensive: ClassVar[bool]

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome: ...


# Pipeline order. Discovery creates leads and is not a per-lead step.
PIPELINE_ORDER: tuple[str, ...] = (
    "requirements",
    "contact_enrichment",
    "audit",
    "branding",
    "website_builder",
    "quality_check",
    "proposal",
    "outreach",
)

_REGISTRY: dict[str, LeadAgent] = {}


def register(agent: LeadAgent) -> LeadAgent:
    if agent.name not in PIPELINE_ORDER:
        raise ValueError(f"unknown pipeline step: {agent.name}")
    _REGISTRY[agent.name] = agent
    return agent


def registered() -> dict[str, LeadAgent]:
    return dict(_REGISTRY)


def load_agents() -> dict[str, LeadAgent]:
    """Import agent modules so they self-register; returns the registry."""
    import app.agents  # noqa: F401, PLC0415  (side effect: registration)

    return registered()
