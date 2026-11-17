"""Requirements agent against Postgres with the mock LLM."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from app.adapters.registry import build_adapters
from app.agents.requirements.agent import RequirementsAgent, agents_to_run, verified_quotes
from app.agents.requirements.schemas import RequirementsLLM
from app.config import Settings
from app.db.models import Brief, Business, Cost, Evidence, Lead, LeadStatus, Suppression
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

RIVERSIDE = (
    "We're a 3-person plumbing business in Austin, TX 78704. Our current site was made in 2014 "
    "and doesn't work on phones. Need a redesign with online booking. Budget $1,500-2,500. "
    "Please reply with portfolio."
)


def _settings() -> Settings:
    return Settings(_env_file=None, demo_mode=True, data_dir=Path("./.test-data"))  # type: ignore[call-arg]


async def _lead(
    s: Any,
    *,
    title: str,
    text: str,
    trigger: str = "explicit_request",
    country: str | None = "US",
    language: str | None = "en",
    business: Business | None = None,
    author: str = "poster1",
    source: str = "rss",
) -> Lead:
    lead = Lead(
        business_id=business.id if business else None,
        source=source,
        source_url=f"https://forum.test/{uuid.uuid4().hex[:8]}",
        source_item_id=uuid.uuid4().hex,
        trigger_type=trigger,
        title=title,
        raw_text=text,
        country=country,
        language=language,
        service_needed=[],
        pipeline_state={},
    )
    s.add(lead)
    await s.flush()
    s.add(
        Evidence(
            lead_id=lead.id,
            kind="post",
            url=lead.source_url,
            content_hash=uuid.uuid4().hex,
            data={"author": author},
        )
    )
    await s.flush()
    return lead


async def _run(lead_id: uuid.UUID, llm: Any = None) -> Any:
    adapters = build_adapters(_settings())
    if llm is not None:
        adapters.llm = llm
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=adapters, settings=_settings())
        return await runs.execute(ctx, RequirementsAgent(), lead_id)


def test_quote_verification_and_agent_plan() -> None:
    src = "Need a redesign with   online booking.\nBudget $2k"
    assert verified_quotes(["need a redesign with online booking", "We are a big firm"], src) == [
        "need a redesign with online booking"
    ]
    assert agents_to_run(["redesign"], True, "explicit_request") == [
        "contact_enrichment",
        "audit",
        "branding",
        "website_builder",
        "quality_check",
        "proposal",
        "outreach",
    ]
    assert agents_to_run(["seo"], False, "explicit_request") == [
        "contact_enrichment",
        "proposal",
        "outreach",
    ]


async def test_explicit_request_qualifies_with_brief() -> None:
    async with session_scope() as s:
        biz = Business(
            name="Riverside Plumbing Co",
            normalized_name="riverside plumbing",
            domain="riverside-plumbing.example.com",
            country="US",
            has_website=True,
        )
        s.add(biz)
        await s.flush()
        lead = await _lead(
            s,
            title="Looking for a web designer for my plumbing company",
            text=RIVERSIDE,
            business=biz,
        )
    outcome = await _run(lead.id)
    assert outcome.proceed, outcome.reason
    async with session_scope() as s:
        row = await s.get(Lead, lead.id)
        brief = (await s.get(Brief, lead.id)).data  # type: ignore[union-attr]
        assert row.status == LeadStatus.QUALIFIED  # type: ignore[union-attr]
        assert row.intent_score >= 60  # type: ignore[union-attr, operator]
        assert row.timezone == "America/Chicago"  # type: ignore[union-attr]
        assert brief["used_llm"] is True
        assert brief["budget"].startswith("Budget $1,500")
        source = " ".join(f"{row.title}\n{RIVERSIDE}".split()).lower()  # type: ignore[union-attr]
        assert brief["evidence_quotes"]
        assert all(" ".join(q.split()).lower() in source for q in brief["evidence_quotes"])
        assert brief["agents_to_run"][0] == "contact_enrichment"
        assert brief["existing_website"] == "https://riverside-plumbing.example.com"
        assert "openai" in row.pipeline_state["mock_adapters_used"]  # type: ignore[union-attr]


async def test_german_lead_parked_until_country_rules_exist() -> None:
    async with session_scope() as s:
        lead = await _lead(
            s,
            title="Wir suchen einen Webdesigner für unseren Malerbetrieb",
            text="Unser Malerbetrieb in Berlin braucht eine neue Webseite.",
            country="DE",
            language="de",
        )
    outcome = await _run(lead.id)
    assert not outcome.proceed
    assert outcome.status == LeadStatus.PARKED_COUNTRY
    assert "DE not a target country" in outcome.reason


async def test_italian_lead_parked_by_language() -> None:
    async with session_scope() as s:
        lead = await _lead(
            s,
            title="Cerco un web designer",
            text="Ho bisogno di un sito web.",
            country="US",
            language="it",
        )
    outcome = await _run(lead.id)
    assert outcome.status == LeadStatus.PARKED_LANGUAGE


async def test_proactive_trigger_scored_by_rules_without_llm_cost() -> None:
    async with session_scope() as s:
        biz = Business(
            name="Oakfield Roofing",
            normalized_name="oakfield roofing",
            country="GB",
            address="Leeds LS6 2AB",
            has_website=False,
        )
        s.add(biz)
        await s.flush()
        lead = await _lead(
            s,
            title="Oakfield Roofing — listed without a website",
            text="Listed in a directory without a website.",
            trigger="no_website",
            country="GB",
            business=biz,
        )
    outcome = await _run(lead.id)
    assert outcome.proceed, outcome.reason
    async with session_scope() as s:
        brief = (await s.get(Brief, lead.id)).data  # type: ignore[union-attr]
        assert brief["used_llm"] is False
        assert brief["services"] == ["website"]
        assert brief["intent"]["llm_score"] is None
        assert "audit" not in brief["agents_to_run"]  # no site to audit
        assert (await s.execute(select(func.count()).select_from(Cost))).scalar_one() == 0


async def test_seller_post_rejected_and_platform_identity_exact() -> None:
    async with session_scope() as s:
        lead = await _lead(
            s,
            title="[For Hire] I design logos and websites",
            text="I design logos and websites for small businesses. DM me for my portfolio.",
            author="designer_dave",
            source="reddit",
        )
    outcome = await _run(lead.id)
    assert outcome.status == LeadStatus.REJECTED_LOW_INTENT
    async with session_scope() as s:
        row = await s.get(Lead, lead.id)
        biz = await s.get(Business, row.business_id)  # type: ignore[union-attr, arg-type]
        assert (biz.registry_source, biz.registry_number) == ("handle:reddit", "designer_dave")  # type: ignore[union-attr]


class _LyingLLM:
    """Returns a confident answer with a fabricated quote and a URL not in the text."""

    name: ClassVar[str] = "lying"
    is_mock: ClassVar[bool] = True

    async def structured(self, session: Any, request: Any, schema: type[BaseModel]) -> Any:
        return RequirementsLLM(
            is_buying_intent=True,
            intent_score=95,
            intent_reasoning="sure",
            services=["website"],
            existing_website="https://made-up-site.com",
            evidence_quotes=["We will pay $50,000 tomorrow"],
            recipient_type="unknown",
        )


async def test_fabricated_quotes_and_urls_are_discarded_and_intent_capped() -> None:
    async with session_scope() as s:
        lead = await _lead(
            s,
            title="Thinking about websites",
            text="Do small shops even need a website these days? Curious, not hiring.",
        )
    outcome = await _run(lead.id, llm=_LyingLLM())
    assert outcome.status == LeadStatus.REJECTED_LOW_INTENT
    async with session_scope() as s:
        brief = (await s.get(Brief, lead.id)).data  # type: ignore[union-attr]
        assert brief["evidence_quotes"] == []
        assert brief["existing_website"] is None
        assert brief["intent"]["llm_score"] == 35
        assert any("not found in the source text" in a for a in brief["assumptions"])
        assert any("website URL not present" in a for a in brief["assumptions"])


class _CountingLLM(_LyingLLM):
    calls = 0

    async def structured(self, session: Any, request: Any, schema: type[BaseModel]) -> Any:
        type(self).calls += 1
        return await super().structured(session, request, schema)


async def test_irrelevant_post_never_reaches_the_llm() -> None:
    """Free-tier guard: no request for our services → rules only, zero intent, no LLM call."""
    async with session_scope() as s:
        lead = await _lead(
            s, title="Our new van arrived", text="Big week for the team, thanks everyone!"
        )
    _CountingLLM.calls = 0
    outcome = await _run(lead.id, llm=_CountingLLM())
    assert outcome.status == LeadStatus.REJECTED_LOW_INTENT
    assert _CountingLLM.calls == 0
    async with session_scope() as s:
        brief = (await s.get(Brief, lead.id)).data  # type: ignore[union-attr]
        assert brief["intent"]["llm_score"] == 0
        assert any("LLM not called" in a for a in brief["assumptions"])


async def test_duplicate_and_suppressed_businesses_stop() -> None:
    async with session_scope() as s:
        biz = Business(name="Sparkle Clean", normalized_name="sparkle clean", country="US")
        s.add(biz)
        await s.flush()
        first = await _lead(
            s,
            title="Need a new website for my cleaning company",
            text="Looking for a web designer. Budget $2,000.",
            business=biz,
        )
        second = await _lead(
            s,
            title="Need a new website asap",
            text="Looking for a web designer, budget $1500",
            business=biz,
        )
    assert (await _run(first.id)).status == LeadStatus.QUALIFIED
    dup = await _run(second.id)
    assert dup.status == LeadStatus.DUPLICATE
    async with session_scope() as s:
        blocked = Business(name="Blocked Co", normalized_name="blocked", country="US")
        s.add(blocked)
        await s.flush()
        s.add(Suppression(business_id=blocked.id, reason="unsubscribe"))
        third = await _lead(
            s,
            title="Need a website",
            text="Looking for a web designer. Budget $900.",
            business=blocked,
        )
    assert (await _run(third.id)).status == LeadStatus.SUPPRESSED


async def test_rerun_is_idempotent() -> None:
    async with session_scope() as s:
        lead = await _lead(
            s, title="Need a new website", text="Looking for a web designer. Budget $1,200."
        )
    await _run(lead.id)
    await _run(lead.id)
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Brief))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(Business))).scalar_one() == 1
