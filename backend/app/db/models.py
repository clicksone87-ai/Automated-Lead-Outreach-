"""SQLAlchemy models.

Supporting tables beyond §6:
- `policy_decisions`   every policy check with its reason (§8 "logged with its reason")
- `settings_changes`   audit log of settings / threshold changes (§13)
- `sources`            per-source enable flag, schedule, health (§7, §14 Sources page)
- `source_runs`        each discovery run of a source
- `outreach_locks`     one active outreach per business (§9, row-level guarantee)
- `activity_events`    live activity feed streamed to the dashboard
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ─── Enumerations (stored as strings; validated in Python) ──────────────────


class TriggerType(StrEnum):
    EXPLICIT_REQUEST = "explicit_request"
    NEW_BUSINESS = "new_business"
    NO_WEBSITE = "no_website"
    BROKEN_OR_OUTDATED_SITE = "broken_or_outdated_site"
    PROFILE_WITHOUT_SITE = "profile_without_site"


class Service(StrEnum):
    LOGO = "logo"
    WEBSITE = "website"
    REDESIGN = "redesign"
    SEO = "seo"


class LeadStatus(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    REJECTED_LOW_INTENT = "rejected_low_intent"
    PARKED_LANGUAGE = "parked_language"
    DUPLICATE = "duplicate"
    COOLDOWN = "cooldown"
    SUPPRESSED = "suppressed"
    NO_CONTACT_ROUTE = "no_contact_route"
    CONTACT_FOUND = "contact_found"
    AUDITED = "audited"
    BRANDED = "branded"
    BUILT = "built"
    QA_PASSED = "qa_passed"
    NEEDS_REVIEW = "needs_review"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED_BY_HUMAN = "rejected_by_human"
    SENT = "sent"
    REPLIED = "replied"
    WON = "won"
    LOST = "lost"
    CLOSED = "closed"
    FAILED = "failed"
    PAUSED_BUDGET = "paused_budget"


class EvidenceKind(StrEnum):
    POST = "post"
    PAGE = "page"
    SCREENSHOT = "screenshot"
    METRIC = "metric"
    REGISTRY_RECORD = "registry_record"


class ContactType(StrEnum):
    EMAIL = "email"
    CONTACT_FORM = "contact_form"
    PLATFORM_REPLY = "platform_reply"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VALID = "valid"
    INVALID = "invalid"
    CATCH_ALL = "catch_all"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class QAStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class ApprovalStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReplyClass(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNSUBSCRIBE = "unsubscribe"
    QUESTION = "question"
    AUTO_REPLY = "auto_reply"
    OTHER = "other"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


# ─── Core entities ──────────────────────────────────────────────────────────


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    registry_number: Mapped[str | None] = mapped_column(String(100))
    registry_source: Mapped[str | None] = mapped_column(String(50))
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    language: Mapped[str | None] = mapped_column(String(8))
    timezone: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(100), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(50))
    has_website: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(30), default="active")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    leads: Mapped[list[Lead]] = relationship(back_populates="business")
    contacts: Mapped[list[Contact]] = relationship(back_populates="business")

    __table_args__ = (
        UniqueConstraint("registry_source", "registry_number", name="uq_business_registry"),
        Index(
            "ix_business_name_trgm",
            "normalized_name",
            postgresql_using="gin",
            postgresql_ops={"normalized_name": "gin_trgm_ops"},
        ),
    )


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(50), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_item_id: Mapped[str] = mapped_column(String(300))
    trigger_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    service_needed: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    intent_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=LeadStatus.DISCOVERED, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    language: Mapped[str | None] = mapped_column(String(8), index=True)
    timezone: Mapped[str | None] = mapped_column(String(64))
    pipeline_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    business: Mapped[Business | None] = relationship(back_populates="leads")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="lead")

    __table_args__ = (UniqueConstraint("source", "source_item_id", name="uq_lead_source_item"),)

    @property
    def evidence_ids(self) -> list[uuid.UUID]:
        return [e.id for e in self.evidence]


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    url: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    excerpt: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    captured_at: Mapped[datetime] = _created()

    lead: Mapped[Lead | None] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint(
            "lead_id",
            "kind",
            "content_hash",
            name="uq_evidence_lead_hash",
            postgresql_nulls_not_distinct=True,
        ),
    )


class Brief(Base):
    __tablename__ = "briefs"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(
        String(20), default=VerificationStatus.UNVERIFIED
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    business: Mapped[Business] = relationship(back_populates="contacts")

    __table_args__ = (UniqueConstraint("business_id", "type", "value", name="uq_contact"),)


class Audit(Base):
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True
    )
    url: Mapped[str] = mapped_column(Text)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    findings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    screenshots: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    competitor_comparison: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    before_after_summary: Mapped[str | None] = mapped_column(Text)
    report_pdf_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class BrandProfile(Base):
    __tablename__ = "brand_profiles"

    id: Mapped[uuid.UUID] = _pk()
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), unique=True
    )
    colors: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    fonts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    logo_path: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(100))
    competitor_styles: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class LogoConcept(Base):
    __tablename__ = "logo_concepts"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    variant: Mapped[int] = mapped_column(Integer)
    svg_path: Mapped[str | None] = mapped_column(Text)
    png_path: Mapped[str | None] = mapped_column(Text)
    palette: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    typography: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    qa_status: Mapped[str] = mapped_column(String(20), default=QAStatus.PENDING)
    qa_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (UniqueConstraint("lead_id", "variant", name="uq_logo_variant"),)


class Preview(Base):
    __tablename__ = "previews"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True
    )
    template_id: Mapped[str] = mapped_column(String(100))
    source_path: Mapped[str | None] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text)
    screenshots: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    test_results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    qa_status: Mapped[str] = mapped_column(String(20), default=QAStatus.PENDING)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    language: Mapped[str] = mapped_column(String(8))
    subject: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    attachments: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    claims: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    template_variant: Mapped[str | None] = mapped_column(String(50))
    approval_status: Mapped[str] = mapped_column(String(20), default=ApprovalStatus.DRAFT)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (UniqueConstraint("lead_id", "version", name="uq_proposal_version"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _pk()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )
    to_email: Mapped[str] = mapped_column(String(320))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider_id: Mapped[str | None] = mapped_column(String(200), index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.QUEUED)
    bounce_type: Mapped[str | None] = mapped_column(String(50))
    complained: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_number: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(500), unique=True)
    from_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[str] = mapped_column(Text)
    classification: Mapped[str | None] = mapped_column(String(20))
    handed_to_inbox: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Suppression(Base):
    """Permanent. Rows are never deleted by the application."""

    __tablename__ = "suppression_list"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str | None] = mapped_column(String(320))
    domain: Mapped[str | None] = mapped_column(String(255))
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("businesses.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index(
            "uq_suppression_email", "email", unique=True, postgresql_where=text("email IS NOT NULL")
        ),
        Index(
            "uq_suppression_domain",
            "domain",
            unique=True,
            postgresql_where=text("domain IS NOT NULL AND email IS NULL"),
        ),
        Index(
            "uq_suppression_business",
            "business_id",
            unique=True,
            postgresql_where=text("business_id IS NOT NULL AND email IS NULL"),
        ),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _pk()
    agent: Mapped[str] = mapped_column(String(50), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 4, asdecimal=False), default=0)
    created_at: Mapped[datetime] = _created()


class Cost(Base):
    __tablename__ = "costs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    agent: Mapped[str] = mapped_column(String(50), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 4, asdecimal=False))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _updated()


class SettingChange(Base):
    __tablename__ = "settings_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    old_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSONB)
    changed_by: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class PricingPackage(Base):
    __tablename__ = "pricing_packages"

    id: Mapped[uuid.UUID] = _pk()
    service: Mapped[str] = mapped_column(String(20))
    package: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    price_usd: Mapped[float] = mapped_column(Numeric(10, 2, asdecimal=False))
    price_eur: Mapped[float] = mapped_column(Numeric(10, 2, asdecimal=False))
    price_gbp: Mapped[float | None] = mapped_column(Numeric(10, 2, asdecimal=False))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("service", "package", name="uq_pricing"),)


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = _pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), unique=True
    )
    quote: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    contract_status: Mapped[str] = mapped_column(String(20), default="none")
    payment_status: Mapped[str] = mapped_column(String(20), default="none")
    delivery_status: Mapped[str] = mapped_column(String(20), default="none")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# ─── Supporting tables ──────────────────────────────────────────────────────


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(50))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )
    allowed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Source(Base):
    __tablename__ = "sources"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_minutes: Mapped[int] = mapped_column(Integer, default=30)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    leads_found_total: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = _updated()


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(ForeignKey("sources.key"), index=True)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    leads_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class OutreachLock(Base):
    """At most one active outreach per business (primary key = business_id)."""

    __tablename__ = "outreach_locks"

    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    acquired_at: Mapped[datetime] = _created()


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    agent: Mapped[str | None] = mapped_column(String(50))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


__all__ = [
    "ActivityEvent",
    "AgentRun",
    "ApprovalStatus",
    "Audit",
    "Base",
    "BrandProfile",
    "Brief",
    "Business",
    "Contact",
    "ContactType",
    "Cost",
    "Deal",
    "DeliveryStatus",
    "Evidence",
    "EvidenceKind",
    "Lead",
    "LeadStatus",
    "LogoConcept",
    "Message",
    "OutreachLock",
    "PolicyDecision",
    "Preview",
    "PricingPackage",
    "Proposal",
    "QAStatus",
    "Reply",
    "ReplyClass",
    "RunStatus",
    "Service",
    "SettingChange",
    "SettingRow",
    "Source",
    "SourceRun",
    "Suppression",
    "TriggerType",
    "VerificationStatus",
]
