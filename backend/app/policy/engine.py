"""Policy and safety layer. Plain code — AI can never override a decision.

Every check returns a PolicyResult and is logged to `policy_decisions` with its reason.
`evaluate_outreach()` runs the full pre-send gate; any failure blocks the send.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, integration_status
from app.db.models import (
    Business,
    DeliveryStatus,
    Lead,
    LogoConcept,
    Message,
    OutreachLock,
    PolicyDecision,
    Preview,
    QAStatus,
    Suppression,
)
from app.policy.country_rules import CountryRules, get_rules
from app.policy.restricted import is_restricted
from app.services import runtime_settings
from app.services.runtime_settings import RuntimeSettings

RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")


@dataclass(frozen=True, slots=True)
class PolicyResult:
    check: str
    allowed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyVerdict:
    results: list[PolicyResult]

    @property
    def allowed(self) -> bool:
        return all(r.allowed for r in self.results)

    @property
    def failures(self) -> list[PolicyResult]:
        return [r for r in self.results if not r.allowed]


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


class PolicyEngine:
    def __init__(self, session: AsyncSession, rules: CountryRules | None = None) -> None:
        self.session = session
        self.rules = rules or get_rules()

    async def _log(
        self, result: PolicyResult, *, action: str, lead_id: uuid.UUID | None
    ) -> PolicyResult:
        self.session.add(
            PolicyDecision(
                check=result.check, action=action, lead_id=lead_id, allowed=result.allowed,
                reason=result.reason, details=result.details,
            )
        )
        await self.session.flush()
        return result

    # ── individual checks ─────────────────────────────────────────────────

    async def check_suppression(
        self, *, action: str, lead_id: uuid.UUID | None, email: str | None = None,
        business_id: uuid.UUID | None = None,
    ) -> PolicyResult:
        conds = []
        if email:
            conds.append(func.lower(Suppression.email) == email.lower())
            conds.append(
                (Suppression.domain == email_domain(email)) & Suppression.email.is_(None)
            )
        if business_id:
            conds.append(Suppression.business_id == business_id)
        hit = None
        if conds:
            hit = (await self.session.execute(
                select(Suppression).where(or_(*conds)).limit(1)
            )).scalar_one_or_none()
        result = (
            PolicyResult("suppression", False, f"on suppression list ({hit.reason})",
                         {"suppression_id": str(hit.id)})
            if hit else PolicyResult("suppression", True, "not suppressed")
        )
        return await self._log(result, action=action, lead_id=lead_id)

    async def check_country(
        self, *, action: str, lead: Lead, purpose: str, recipient_type: str = "unknown"
    ) -> PolicyResult:
        rule = self.rules.get(lead.country)
        if rule is None:
            result = PolicyResult("country", False, f"no rules loaded for country {lead.country!r}")
        elif purpose == "draft":
            result = PolicyResult(
                "country", rule.can_draft,
                "country enabled for drafting" if rule.can_draft else "country disabled",
            )
        elif not rule.enabled:
            result = PolicyResult("country", False, "country disabled")
        elif not rule.legal_reviewed:
            result = PolicyResult("country", False, "country rules not legally reviewed yet")
        elif rule.requires_prior_consent:
            result = PolicyResult("country", False, "country requires prior consent")
        elif recipient_type not in rule.allowed_recipient_types:
            result = PolicyResult(
                "country", False,
                f"recipient type {recipient_type!r} not allowed in {lead.country}",
                {"allowed": list(rule.allowed_recipient_types)},
            )
        else:
            result = PolicyResult("country", True, f"{lead.country} rules allow sending")
        return await self._log(result, action=action, lead_id=lead.id)

    async def check_language(
        self, *, action: str, lead: Lead, purpose: str, rs: RuntimeSettings
    ) -> PolicyResult:
        allowed_langs = rs.sending_languages if purpose == "send" else rs.enabled_languages
        ok = bool(lead.language) and lead.language in allowed_langs
        result = PolicyResult(
            "language", ok,
            f"language {lead.language!r} {'enabled' if ok else 'not enabled'} for {purpose}",
            {"enabled": allowed_langs},
        )
        return await self._log(result, action=action, lead_id=lead.id)

    async def check_cooldown_and_lock(
        self, *, action: str, lead: Lead, now: datetime | None = None
    ) -> PolicyResult:
        now = now or datetime.now(UTC)
        if lead.business_id is None:
            result = PolicyResult("duplicate_cooldown", False, "lead has no business record")
            return await self._log(result, action=action, lead_id=lead.id)
        business = await self.session.get(Business, lead.business_id)
        lock = await self.session.get(OutreachLock, lead.business_id)
        if business and business.cooldown_until and business.cooldown_until > now:
            result = PolicyResult(
                "duplicate_cooldown", False,
                f"business in cooldown until {business.cooldown_until.isoformat()}",
            )
        elif lock and lock.lead_id != lead.id:
            result = PolicyResult(
                "duplicate_cooldown", False, "business already has an active outreach",
                {"active_lead_id": str(lock.lead_id)},
            )
        else:
            result = PolicyResult("duplicate_cooldown", True, "no cooldown or competing outreach")
        return await self._log(result, action=action, lead_id=lead.id)

    async def check_send_limits(
        self, *, action: str, lead_id: uuid.UUID | None, rs: RuntimeSettings,
        now: datetime | None = None,
    ) -> PolicyResult:
        now = now or datetime.now(UTC)
        sent_states = [DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.BOUNCED,
                       DeliveryStatus.COMPLAINED]

        async def count_since(since: datetime) -> int:
            return int((await self.session.execute(
                select(func.count()).select_from(Message).where(
                    Message.sent_at >= since, Message.delivery_status.in_(sent_states)
                )
            )).scalar_one())

        first_sent = (await self.session.execute(select(func.min(Message.sent_at)))).scalar_one()
        week = 0 if first_sent is None else max(0, (now - first_sent).days // 7)
        warmup_cap = rs.warmup_schedule[min(week, len(rs.warmup_schedule) - 1)] \
            if rs.warmup_schedule else rs.daily_send_limit
        daily_cap = min(rs.daily_send_limit, warmup_cap)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = await count_since(today)
        sent_hour = await count_since(now - timedelta(hours=1))
        details = {"sent_today": sent_today, "daily_cap": daily_cap, "sent_last_hour": sent_hour,
                   "hourly_cap": rs.hourly_send_limit, "warmup_week": week}
        if sent_today >= daily_cap:
            result = PolicyResult("send_limits", False, "daily send limit reached", details)
        elif sent_hour >= rs.hourly_send_limit:
            result = PolicyResult("send_limits", False, "hourly send limit reached", details)
        else:
            result = PolicyResult("send_limits", True, "within send limits", details)
        return await self._log(result, action=action, lead_id=lead_id)

    async def check_qa_gate(self, *, action: str, lead: Lead) -> PolicyResult:
        failed_logos = (await self.session.execute(
            select(func.count()).select_from(LogoConcept).where(
                LogoConcept.lead_id == lead.id, LogoConcept.qa_status != QAStatus.PASSED
            )
        )).scalar_one()
        preview = (await self.session.execute(
            select(Preview).where(Preview.lead_id == lead.id)
        )).scalar_one_or_none()
        problems = []
        if failed_logos:
            problems.append(f"{failed_logos} logo concept(s) not QA-passed")
        if preview is not None and preview.qa_status != QAStatus.PASSED:
            problems.append("website preview not QA-passed")
        result = PolicyResult(
            "qa_gate", not problems, "; ".join(problems) or "all drafts passed quality check"
        )
        return await self._log(result, action=action, lead_id=lead.id)

    async def check_recipient(
        self, *, action: str, lead: Lead, email: str, demo_mode: bool
    ) -> PolicyResult:
        domain = email_domain(email)
        reserved = domain.endswith(RESERVED_DOMAINS) or domain.endswith(RESERVED_TLDS)
        mock_used = bool(lead.pipeline_state.get("mock_adapters_used")) or lead.source.startswith(
            "mock"
        )
        if mock_used and not demo_mode:
            result = PolicyResult("demo_data", False, "lead was produced with mock adapters")
        elif mock_used and not reserved:
            result = PolicyResult(
                "demo_data", False, "mock-produced content may only go to reserved test domains"
            )
        elif reserved and not demo_mode:
            result = PolicyResult("demo_data", False, "reserved test domain outside demo mode")
        else:
            result = PolicyResult("demo_data", True, "recipient and content are real/consistent")
        return await self._log(result, action=action, lead_id=lead.id)

    async def check_sender_identity(self, *, action: str, lead_id: uuid.UUID) -> PolicyResult:
        configured = integration_status(get_settings())["sender_identity"].configured
        result = PolicyResult(
            "sender_identity", configured,
            "sender identity configured" if configured
            else "sender name/business/postal address/portfolio missing",
        )
        return await self._log(result, action=action, lead_id=lead_id)

    async def check_restricted(self, *, action: str, lead_id: uuid.UUID | None) -> PolicyResult:
        restricted = is_restricted(action)
        result = PolicyResult(
            "restricted_action", not restricted,
            "restricted action — manual only" if restricted else "action not restricted",
        )
        return await self._log(result, action=action, lead_id=lead_id)

    # ── composite gates ───────────────────────────────────────────────────

    async def evaluate_drafting(self, lead: Lead) -> PolicyVerdict:
        """Before spending money on drafts (audit/branding/builder/proposal)."""
        rs = await runtime_settings.load(self.session)
        action = "draft"
        results = [
            await self.check_country(action=action, lead=lead, purpose="draft"),
            await self.check_language(action=action, lead=lead, purpose="process", rs=rs),
            await self.check_suppression(action=action, lead_id=lead.id,
                                         business_id=lead.business_id),
            await self.check_cooldown_and_lock(action=action, lead=lead),
        ]
        return PolicyVerdict(results)

    async def evaluate_outreach(
        self, lead: Lead, *, email: str, recipient_type: str
    ) -> PolicyVerdict:
        """The full pre-send gate. All checks run and are logged; any failure blocks."""
        rs = await runtime_settings.load(self.session)
        demo_mode = get_settings().demo_mode
        action = "send_email"
        results = [
            await self.check_restricted(action=action, lead_id=lead.id),
            await self.check_suppression(action=action, lead_id=lead.id, email=email,
                                         business_id=lead.business_id),
            await self.check_country(action=action, lead=lead, purpose="send",
                                     recipient_type=recipient_type),
            await self.check_language(action=action, lead=lead, purpose="send", rs=rs),
            await self.check_cooldown_and_lock(action=action, lead=lead),
            await self.check_send_limits(action=action, lead_id=lead.id, rs=rs),
            await self.check_qa_gate(action=action, lead=lead),
            await self.check_recipient(action=action, lead=lead, email=email,
                                       demo_mode=demo_mode),
            await self.check_sender_identity(action=action, lead_id=lead.id),
        ]
        return PolicyVerdict(results)
