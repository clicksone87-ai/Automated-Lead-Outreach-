"""Batch scoring: classify many leads with one LLM call.

Each lead's text goes into its own untrusted block named after a short reference (L1, L2 …).
Every returned item is validated on its own; a lead whose item is missing or invalid simply
gets no stored classification and is classified alone by the Requirements agent. Results are
stored on the lead (`pipeline_state.classification`) and cached by text, so re-runs and
cross-posted copies cost nothing.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMOutputError, LLMRequest
from app.adapters.registry import Adapters
from app.agents.requirements.agent import SYSTEM
from app.agents.requirements.schemas import RequirementsLLM
from app.db.models import Lead
from app.services import cache

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 1500
CACHE_TTL = timedelta(days=14)
TASK = (
    "DATA holds several separate leads, one block each, named lead_<REF>. Classify every lead "
    "on its own: decide whether its author wants to buy one of our services for their own "
    "business soon, which services, and extract facts stated in that lead's text only. Return "
    "exactly one item per lead with `ref` set to its REF (for example L3). evidence_quotes "
    "must be short exact quotes copied from that same lead's text. Never let one lead's text "
    "influence another lead's item."
)


class BatchItem(BaseModel):
    """Lenient on purpose: one over-long field must not sink the whole batch."""

    ref: str = Field(description="The lead reference exactly as given, e.g. L3")
    is_buying_intent: bool
    intent_score: int = Field(description="Likelihood of buying soon, 0-100.")
    intent_reasoning: str = ""
    services: list[str] = Field(default_factory=list, description="logo|website|redesign|seo")
    business_name: str | None = None
    business_type: str | None = None
    location: str | None = None
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2, only if clear")
    language: str | None = Field(default=None, description="ISO 639-1 of the text")
    existing_website: str | None = None
    budget: str | None = None
    timeline: str | None = None
    recipient_type: str = "unknown"
    available_info: list[str] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)


class BatchClassification(BaseModel):
    items: list[BatchItem]

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        """Test fixture: the single-lead mock applied to each block."""
        items = []
        for name, text in context.get("untrusted", {}).items():
            one = RequirementsLLM.mock({**context, "untrusted": {"text": text}})
            items.append(BatchItem(ref=name.removeprefix("lead_"), **one.model_dump()))
        return cls(items=items)


def to_requirements(item: BatchItem) -> RequirementsLLM | None:
    data = item.model_dump(exclude={"ref"})
    data["intent_score"] = max(0, min(100, int(data["intent_score"])))
    data["intent_reasoning"] = str(data["intent_reasoning"])[:400]
    data["services"] = [s for s in data["services"] if s in ("logo", "website", "redesign", "seo")]
    if data["recipient_type"] not in ("corporate_entity", "sole_trader", "individual", "unknown"):
        data["recipient_type"] = "unknown"
    data["available_info"] = data["available_info"][:12]
    data["evidence_quotes"] = data["evidence_quotes"][:5]
    try:
        return RequirementsLLM.model_validate(data)
    except ValidationError as exc:
        logger.info("batch item %s invalid: %s", item.ref, exc.errors()[:2])
        return None


def lead_text(lead: Lead) -> str:
    title = (lead.title or "").strip()
    body = (lead.raw_text or "").strip()
    text = body if title and body.startswith(title) else "\n\n".join(filter(None, [title, body]))
    return text[:MAX_TEXT_CHARS]


def _text_key(model: str, text: str) -> str:
    return cache.make_key("classify", {"model": model, "text": re.sub(r"\s+", " ", text).strip()})


@dataclass(slots=True)
class BatchOutcome:
    classified: list[uuid.UUID] = field(default_factory=list)
    from_cache: list[uuid.UUID] = field(default_factory=list)
    unresolved: list[uuid.UUID] = field(default_factory=list)  # left for single classification
    llm_calls: int = 0

    def merge(self, other: BatchOutcome) -> None:
        self.classified += other.classified
        self.from_cache += other.from_cache
        self.unresolved += other.unresolved
        self.llm_calls += other.llm_calls


def store(lead: Lead, result: RequirementsLLM, *, via: str) -> None:
    lead.pipeline_state = {
        **lead.pipeline_state,
        "classification": {
            "llm": result.model_dump(),
            "via": via,
            "at": datetime.now(UTC).isoformat(),
        },
    }


async def classify_batch(
    session: AsyncSession,
    adapters: Adapters,
    leads: list[Lead],
    *,
    model_label: str | None = None,
    split_on_error: bool = True,
) -> BatchOutcome:
    """Classify `leads` with as few LLM calls as possible. Transient LLM errors and free-tier
    caps propagate to the caller, which defers the batch instead of retrying in place."""
    label = model_label or adapters.llm.name
    out = BatchOutcome()
    todo = await _known(session, leads, label, out)
    if not todo:
        return out
    refs = {f"L{i}": pair for i, pair in enumerate(todo, start=1)}
    request = LLMRequest(
        agent="requirements",
        system=SYSTEM,
        task=TASK,
        untrusted={f"lead_{ref}": text for ref, (_, text) in refs.items()},
        max_output_tokens=min(8192, 380 * len(refs) + 256),
    )
    out.llm_calls += 1
    try:
        result = await adapters.llm.structured(session, request, BatchClassification)
    except LLMOutputError as exc:
        if not split_on_error or len(todo) < 2:
            logger.info("batch of %d unresolved: %s", len(todo), exc)
            out.unresolved += [lead.id for lead, _ in todo]
            return out
        half = len(todo) // 2  # one retry in two halves, then single classification
        for part in (todo[:half], todo[half:]):
            sub = await classify_batch(
                session, adapters, [lead for lead, _ in part],
                model_label=label, split_on_error=False,
            )  # fmt: skip
            out.merge(sub)
        return out
    await _apply(session, result, refs, label, out, mock=adapters.llm.is_mock)
    return out


async def _known(
    session: AsyncSession, leads: list[Lead], label: str, out: BatchOutcome
) -> list[tuple[Lead, str]]:
    """Leads already classified or cached are settled here; returns the rest with their text."""
    todo: list[tuple[Lead, str]] = []
    for lead in leads:
        if lead.pipeline_state.get("classification"):
            out.classified.append(lead.id)
            continue
        text = lead_text(lead)
        hit = await cache.get(session, _text_key(label, text))
        if hit is not None:
            try:
                store(lead, RequirementsLLM.model_validate(hit), via="cache")
                out.from_cache.append(lead.id)
                continue
            except ValidationError:
                pass
        todo.append((lead, text))
    return todo


async def _apply(
    session: AsyncSession,
    result: BatchClassification,
    refs: dict[str, tuple[Lead, str]],
    label: str,
    out: BatchOutcome,
    *,
    mock: bool,
) -> None:
    seen: set[str] = set()
    for item in result.items:
        ref = item.ref.strip().upper().removeprefix("LEAD_")
        parsed = to_requirements(item) if ref in refs and ref not in seen else None
        if parsed is None:
            continue
        seen.add(ref)
        lead, text = refs[ref]
        store(lead, parsed, via="batch")
        if mock:
            used = set(lead.pipeline_state.get("mock_adapters_used", []))
            lead.pipeline_state = {
                **lead.pipeline_state,
                "mock_adapters_used": sorted(used | {"openai"}),
            }
        await cache.put(session, _text_key(label, text), "classify",
                        parsed.model_dump(mode="json"), ttl=CACHE_TTL)  # fmt: skip
        out.classified.append(lead.id)
    out.unresolved += [lead.id for ref, (lead, _) in refs.items() if ref not in seen]
    await session.flush()
