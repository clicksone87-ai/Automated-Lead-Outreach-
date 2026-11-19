"""Batch scoring: many leads per LLM call, per-lead validation, cache, and fallback."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMOutputError, LLMRequest
from app.adapters.registry import get_adapters
from app.agents.requirements.agent import RequirementsAgent
from app.agents.requirements.batch import BatchClassification, BatchItem, classify_batch
from app.config import get_settings
from app.db.models import Lead, LeadStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

BUYER = "Looking for a web designer to build a new website for my plumbing company in Austin. Budget $2000."


class FakeLLM:
    """Answers batch requests from a script: ref -> item fields (or None = leave out)."""

    name: ClassVar[str] = "fake"
    is_mock: ClassVar[bool] = False

    def __init__(
        self, script: dict[str, dict[str, Any] | None] | None = None, fail: int = 0
    ) -> None:
        self.script = script or {}
        self.fail = fail
        self.requests: list[LLMRequest] = []

    async def structured(
        self, session: AsyncSession, request: LLMRequest, schema: type[BaseModel]
    ) -> Any:
        self.requests.append(request)
        if self.fail:
            self.fail -= 1
            raise LLMOutputError("invalid structured output")
        items = []
        for name, text in request.untrusted.items():
            ref = name.removeprefix("lead_")
            scripted = self.script.get(ref, {})
            if scripted is None:
                continue
            fields = dict(scripted)
            quote = text.split(".")[0]
            base: dict[str, Any] = {"ref": ref, "is_buying_intent": True, "intent_score": 80,
                                    "intent_reasoning": "asks to hire", "services": ["website"],
                                    "country": "US", "language": "en", "evidence_quotes": [quote]}  # fmt: skip
            items.append(BatchItem(**{**base, **fields}))
        return BatchClassification(items=items)


async def _leads(n: int, text: str = BUYER) -> list[uuid.UUID]:
    ids = []
    async with session_scope() as s:
        for i in range(n):
            lead = Lead(source="rss", source_url=f"https://forum.test/{uuid.uuid4().hex}",
                        source_item_id=uuid.uuid4().hex, trigger_type="explicit_request",
                        title=f"Post {i}", raw_text=f"{text} Ref {i}." if i else text,
                        country="US", language="en", pipeline_state={})  # fmt: skip
            s.add(lead)
            await s.flush()
            ids.append(lead.id)
    return ids


async def _classify(ids: list[uuid.UUID], llm: Any) -> Any:
    adapters = dataclasses.replace(get_adapters(), llm=llm)
    async with session_scope() as s:
        leads = list((await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars())
        return await classify_batch(s, adapters, leads)


async def test_one_call_for_many_leads_then_requirements_needs_no_llm() -> None:
    ids = await _leads(6)
    llm = FakeLLM()
    out = await _classify(ids, llm)
    assert out.llm_calls == 1 and len(llm.requests) == 1
    assert sorted(out.classified) == sorted(ids) and not out.unresolved
    request = llm.requests[0]
    assert sorted(request.untrusted) == sorted(f"lead_L{i}" for i in range(1, 7))  # one block each

    adapters = dataclasses.replace(get_adapters(), llm=llm)
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=adapters, settings=get_settings())
        outcome = await runs.execute(ctx, RequirementsAgent(), ids[0])
    assert len(llm.requests) == 1  # no extra call: the stored batch result was used
    assert outcome.proceed and outcome.status == LeadStatus.QUALIFIED


async def test_missing_and_invalid_items_are_left_for_single_classification() -> None:
    ids = await _leads(3)
    llm = FakeLLM({"L2": None, "L3": {"ref": "L9"}})  # L2 left out, L3 under an unknown ref
    out = await _classify(ids, llm)
    assert out.classified == [ids[0]]
    assert sorted(out.unresolved) == sorted(ids[1:])
    async with session_scope() as s:
        lead = await s.get(Lead, ids[1])
        assert lead is not None and "classification" not in lead.pipeline_state


async def test_invalid_batch_is_split_once_then_given_up() -> None:
    ids = await _leads(4)
    llm = FakeLLM(fail=1)  # whole batch invalid once; both halves then succeed
    out = await _classify(ids, llm)
    assert out.llm_calls == 3 and sorted(out.classified) == sorted(ids)
    ids2 = await _leads(2, text="Need a logo for my bakery, can anyone recommend a designer")
    out2 = await _classify(ids2, FakeLLM(fail=5))
    assert sorted(out2.unresolved) == sorted(ids2) and out2.llm_calls == 3


async def test_same_text_is_served_from_cache() -> None:
    first = await _leads(1)
    await _classify(first, FakeLLM())
    again = await _leads(1)  # cross-posted copy with identical text
    llm = FakeLLM()
    out = await _classify(again, llm)
    assert out.from_cache == again and llm.requests == []


async def test_quote_from_another_lead_is_discarded() -> None:
    ids = await _leads(2)
    # the model answers L2 with L1's opening sentence: it is not in L2's text, so it is dropped
    llm = FakeLLM({"L2": {"evidence_quotes": ["this sentence is not in the second post at all"]}})
    await _classify(ids, llm)
    adapters = dataclasses.replace(get_adapters(), llm=llm)
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=adapters, settings=get_settings())
        await runs.execute(ctx, RequirementsAgent(), ids[1])
    async with session_scope() as s:
        lead = await s.get(Lead, ids[1])
        assert lead is not None and lead.intent_score is not None and lead.intent_score < 60
