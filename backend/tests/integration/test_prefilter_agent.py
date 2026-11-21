"""Prefilter step: stops sellers, stale posts, repeats and excluded markets without an LLM."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.registry import get_adapters
from app.agents.prefilter.agent import PrefilterAgent
from app.config import get_settings
from app.db.models import EvidenceKind, Lead, LeadStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext
from app.services import evidence, runtime_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

NOW = datetime.now(UTC)


async def _lead(text: str, **kw: object) -> uuid.UUID:
    fields: dict[str, object] = {
        "source": "social_reddit",
        "source_family": "social",
        "trigger_type": "explicit_request",
        "country": "US",
        "language": "en",
        "posted_at": NOW - timedelta(hours=2),
        **kw,
    }
    async with session_scope() as s:
        url = str(fields.pop("source_url", f"https://reddit.test/{uuid.uuid4().hex}"))
        lead = Lead(source_url=url, source_item_id=uuid.uuid4().hex,
                    raw_text=text, pipeline_state={}, **fields)  # fmt: skip
        s.add(lead)
        await s.flush()
        await evidence.capture(s, get_adapters().storage, kind=EvidenceKind.POST, lead_id=lead.id,
                               url=lead.source_url, text=text, file_ext="txt")  # fmt: skip
        return lead.id


async def _run(lead_id: uuid.UUID) -> tuple[bool, Lead]:
    async with session_scope() as s:
        ctx = AgentContext(session=s, adapters=get_adapters(), settings=get_settings())
        outcome = await runs.execute(ctx, PrefilterAgent(), lead_id)
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        return outcome.proceed, lead


async def test_buyer_passes() -> None:
    ok, lead = await _run(
        await _lead("Looking for a web designer for my bakery website, budget $800")
    )
    assert ok and lead.status == LeadStatus.DISCOVERED and lead.prefilter_reason is None


async def test_seller_is_rejected_with_zero_intent() -> None:
    ok, lead = await _run(await _lead("I design websites for local businesses, DM me for a quote"))
    assert not ok
    assert lead.status == LeadStatus.NOT_USEFUL and lead.not_useful_reason == "low_intent"
    assert lead.intent_score == 0 and lead.prefilter_reason and "seller" in lead.prefilter_reason


async def test_stale_social_post_is_rejected() -> None:
    lid = await _lead("Need a new logo for my cafe", posted_at=NOW - timedelta(hours=40))
    ok, lead = await _run(lid)
    assert not ok and "h old" in str(lead.prefilter_reason)
    assert lead.status == LeadStatus.NOT_USEFUL and lead.not_useful_reason == "older_than_8h"


async def test_repeated_text_keeps_only_the_first_lead() -> None:
    text = "Can anyone recommend someone to build a website for my roofing company?"
    first = await _lead(text)
    second = await _lead(text, source="social_x")
    assert (await _run(first))[0]
    ok, lead = await _run(second)
    assert not ok and lead.status == LeadStatus.DUPLICATE


async def test_excluded_market_is_parked_worldwide_otherwise_and_triggers_pass() -> None:
    ok, lead = await _run(await _lead("Need a website for my shop", country="PK"))
    assert not ok and lead.status == LeadStatus.NOT_USEFUL
    assert lead.not_useful_reason == "excluded_market"
    assert lead.prefilter_reason == "PK is an excluded market"
    ok, _ = await _run(await _lead("Need a website for my bakery, budget $500", country="BR"))
    assert ok  # every other country is a market
    ok, _ = await _run(await _lead("", trigger_type="broken_or_outdated_site", source="local_sites",
                                   source_family="google", posted_at=None))  # fmt: skip
    assert ok


async def test_social_age_limit_comes_from_settings_not_the_saved_24h() -> None:
    async with session_scope() as s:  # an older saved value must not loosen the 8-hour rule
        await runtime_settings.update(
            s, {"max_post_age_hours": {"social": 24, "default": 168}}, changed_by="test"
        )
    ok, lead = await _run(
        await _lead("Need a new logo for my cafe", posted_at=NOW - timedelta(hours=10))
    )
    assert not ok and "10 h old" in str(lead.prefilter_reason)
    assert (
        await _run(await _lead("Need a logo for my bakery", posted_at=NOW - timedelta(hours=7)))
    )[0]


async def test_stale_or_undated_social_post_goes_to_not_useful() -> None:
    ok, lead = await _run(await _lead("Need a logo", posted_at=NOW - timedelta(hours=9)))
    assert not ok and lead.status == LeadStatus.NOT_USEFUL
    assert lead.not_useful_reason == "older_than_8h"
    assert str(lead.status_reason).startswith("not useful: older than 8 h")
    # A social post URL counts as social whatever the source.
    ok, lead = await _run(await _lead(
        "Need a website for my shop", source="search_api", source_family="google",
        source_url="https://www.reddit.com/r/Leeds/comments/1abc/x/", posted_at=None,
    ))  # fmt: skip
    assert not ok and lead.not_useful_reason == "older_than_8h"


async def test_freelance_platform_listing_goes_to_not_useful() -> None:
    ok, lead = await _run(await _lead(
        "Website redesign for a contractor", source="search_api", source_family="google",
        source_url="https://www.upwork.com/jobs/~01abc", posted_at=None,
    ))  # fmt: skip
    assert not ok and lead.status == LeadStatus.NOT_USEFUL
    assert lead.not_useful_reason == "freelance"
