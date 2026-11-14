"""Budget caps, dedup (pg_trgm), evidence idempotency, runtime settings — against Postgres."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.adapters.storage import LocalStorage
from app.db.models import Business, Evidence, EvidenceKind, PolicyDecision, SettingChange
from app.db.session import session_scope
from app.orchestrator import budget
from app.services import evidence, runtime_settings
from app.services.dedup import BusinessCandidate, resolve_business

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


async def test_budget_charge_within_and_over_daily_cap() -> None:
    async with session_scope() as s:
        await runtime_settings.update(
            s, {"daily_budget_usd": 1.0, "monthly_budget_usd": 10.0}, changed_by="test"
        )
    async with session_scope() as s:
        await budget.charge(s, provider="openai", agent="requirements", amount_usd=0.6)
        await budget.charge(s, provider="openai", agent="requirements", amount_usd=0.4)
    async with session_scope() as s:
        with pytest.raises(budget.BudgetExceededError) as exc:
            await budget.charge(s, provider="openai", agent="requirements", amount_usd=0.01)
        assert exc.value.scope == "global:daily"
        await s.commit()  # keep the logged refusal
    async with session_scope() as s:
        refused = (
            (await s.execute(select(PolicyDecision).where(PolicyDecision.check == "budget")))
            .scalars()
            .all()
        )
        assert len(refused) == 1
        assert refused[0].allowed is False
        snap = await budget.snapshot(s)
        assert snap.spent_today == pytest.approx(1.0)
        assert snap.paused


async def test_budget_per_agent_cap_and_settle() -> None:
    async with session_scope() as s:
        await runtime_settings.update(
            s, {"budget_caps": {"agent:branding": {"daily_usd": 0.5}}}, changed_by="test"
        )
    async with session_scope() as s:
        reserved = await budget.charge(s, provider="openai", agent="branding", amount_usd=0.4)
        await budget.settle(s, reserved, 0.1)  # actual was cheaper → frees headroom
        await budget.charge(s, provider="openai", agent="branding", amount_usd=0.35)
        with pytest.raises(budget.BudgetExceededError) as exc:
            await budget.charge(s, provider="openai", agent="branding", amount_usd=0.1)
        assert exc.value.scope == "agent:branding:daily"
        # another agent is unaffected by the branding cap
        await budget.charge(s, provider="openai", agent="audit", amount_usd=0.1)


async def test_dedup_matches_domain_registry_and_fuzzy_name() -> None:
    async with session_scope() as s:
        a = await resolve_business(
            s,
            BusinessCandidate(
                name="Smith & Sons Plumbing Ltd",
                domain="https://www.smithplumbing.co.uk/contact",
                country="GB",
            ),
        )
        assert a.created
        by_domain = await resolve_business(
            s,
            BusinessCandidate(
                name="Totally different name", domain="smithplumbing.co.uk", country="GB"
            ),
        )
        assert by_domain.business.id == a.business.id
        assert by_domain.matched_on == "domain"
        by_name = await resolve_business(
            s,
            BusinessCandidate(
                name="SMITH AND SONS PLUMBING LIMITED", country="GB", phone="+44 20 7946 0018"
            ),
        )
        assert by_name.business.id == a.business.id
        assert by_name.matched_on == "name"
        assert by_name.business.phone == "+442079460018"  # gap filled, not overwritten
        other_country = await resolve_business(
            s, BusinessCandidate(name="Smith and Sons Plumbing", country="US")
        )
        assert other_country.created
        reg1 = await resolve_business(
            s,
            BusinessCandidate(
                name="Greenleaf Landscaping Ltd",
                registry_number="123",
                registry_source="ch",
                country="GB",
            ),
        )
        reg2 = await resolve_business(
            s,
            BusinessCandidate(
                name="Greenleaf Gardens", registry_number="123", registry_source="ch", country="GB"
            ),
        )
        assert reg2.business.id == reg1.business.id
        assert reg2.matched_on == "registry"
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Business))).scalar_one() == 3


async def test_evidence_capture_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    async with session_scope() as s:
        first = await evidence.capture(
            s,
            storage,
            kind=EvidenceKind.PAGE,
            url="https://x.test",
            text="hello world",
            file_ext="txt",
        )
        second = await evidence.capture(
            s,
            storage,
            kind=EvidenceKind.PAGE,
            url="https://x.test",
            text="hello world",
            file_ext="txt",
        )
        assert first.id == second.id
        assert storage.read_bytes(first.file_path or "") == b"hello world"
        count = (await s.execute(select(func.count()).select_from(Evidence))).scalar_one()
        assert count == 1


async def test_runtime_settings_log_changes_and_protect_autonomy() -> None:
    async with session_scope() as s:
        rs = await runtime_settings.load(s)
        assert rs.sending_languages == ["en"]
        assert set(rs.enabled_languages) == {"en", "de", "fr", "es"}
        assert rs.target_countries == ["US", "GB"]
        await runtime_settings.update(
            s, {"min_intent_score": 70}, changed_by="test", reason="tighten"
        )
        with pytest.raises(PermissionError):
            await runtime_settings.update(s, {"autonomy_stage": 3}, changed_by="test")
        with pytest.raises(ValueError, match="validation"):
            await runtime_settings.update(s, {"min_intent_score": 500}, changed_by="test")
    async with session_scope() as s:
        assert (await runtime_settings.load(s)).min_intent_score == 70
        change = (await s.execute(select(SettingChange))).scalar_one()
        assert (change.key, change.old_value, change.new_value) == ("min_intent_score", 60, 70)
