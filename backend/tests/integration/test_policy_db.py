"""Policy engine gates against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Business, Lead, OutreachLock, Suppression, TriggerType
from app.db.session import session_scope
from app.policy.engine import PolicyEngine

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def _lead(s, *, country: str = "US", language: str = "en", source: str = "rss") -> Lead:  # type: ignore[no-untyped-def]
    biz = Business(name="Acme Plumbing", normalized_name="acme plumbing", country=country)
    s.add(biz)
    await s.flush()
    lead = Lead(
        business_id=biz.id,
        source=source,
        source_url="https://x.test/1",
        source_item_id=f"i-{biz.id}",
        trigger_type=TriggerType.EXPLICIT_REQUEST,
        country=country,
        language=language,
        pipeline_state={},
    )
    s.add(lead)
    await s.flush()
    return lead


async def test_drafting_allowed_for_us_blocked_for_de_and_unknown() -> None:
    async with session_scope() as s:
        engine = PolicyEngine(s)
        assert (await engine.evaluate_drafting(await _lead(s, country="US"))).allowed
        de = await engine.evaluate_drafting(await _lead(s, country="DE", language="de"))
        assert not de.allowed
        assert any(f.check == "country" for f in de.failures)
        fr = await engine.evaluate_drafting(await _lead(s, country="FR", language="fr"))
        assert "no rules loaded" in next(f.reason for f in fr.failures if f.check == "country")


async def test_parked_language_blocks_drafting() -> None:
    async with session_scope() as s:
        verdict = await PolicyEngine(s).evaluate_drafting(await _lead(s, language="it"))
        assert [f.check for f in verdict.failures] == ["language"]


async def test_outreach_blocked_until_legal_review_and_everything_logged() -> None:
    async with session_scope() as s:
        lead = await _lead(s)
        verdict = await PolicyEngine(s).evaluate_outreach(
            lead, email="owner@acme-plumbing.com", recipient_type="corporate_entity"
        )
        assert not verdict.allowed
        reasons = {f.check: f.reason for f in verdict.failures}
        assert reasons["country"] == "country rules not legally reviewed yet"
        assert "sender_identity" in reasons
        assert len(verdict.results) == 9  # every check ran


async def test_suppression_cooldown_and_competing_outreach() -> None:
    async with session_scope() as s:
        engine = PolicyEngine(s)
        lead = await _lead(s)
        s.add(Suppression(email="owner@acme.com", reason="unsubscribe"))
        await s.flush()
        hit = await engine.check_suppression(action="t", lead_id=lead.id, email="OWNER@acme.com")
        assert not hit.allowed
        s.add(Suppression(domain="blocked.com", reason="complaint"))
        await s.flush()
        dom = await engine.check_suppression(action="t", lead_id=lead.id, email="x@blocked.com")
        assert not dom.allowed

        business = await s.get(Business, lead.business_id)
        assert business is not None
        business.cooldown_until = datetime.now(UTC) + timedelta(days=10)
        await s.flush()
        assert not (await engine.check_cooldown_and_lock(action="t", lead=lead)).allowed
        business.cooldown_until = None
        other = Lead(
            business_id=business.id,
            source="rss",
            source_url="https://x.test/2",
            source_item_id="other",
            trigger_type=TriggerType.NO_WEBSITE,
            pipeline_state={},
        )
        s.add(other)
        await s.flush()
        s.add(OutreachLock(business_id=business.id, lead_id=other.id))
        await s.flush()
        locked = await engine.check_cooldown_and_lock(action="t", lead=lead)
        assert not locked.allowed
        assert locked.reason == "business already has an active outreach"


async def test_mock_produced_lead_never_reaches_real_recipient() -> None:
    async with session_scope() as s:
        engine = PolicyEngine(s)
        lead = await _lead(s, source="mock_demo")
        real = await engine.check_recipient(
            action="t", lead=lead, email="a@realbiz.com", demo_mode=True
        )
        assert not real.allowed
        test_domain = await engine.check_recipient(
            action="t", lead=lead, email="a@example.com", demo_mode=True
        )
        assert test_domain.allowed
