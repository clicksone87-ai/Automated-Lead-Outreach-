"""API response/request models (also the source of the dashboard's typed client)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class IntegrationOut(BaseModel):
    key: str
    label: str
    #: ready | needs_api_key | off_testing_mode | off  (mock: test suite only)
    state: str
    env_vars: list[str]
    paid: bool
    signup_url: str
    note: str = ""
    mock_in_use: bool = False


class HealthOut(BaseModel):
    status: str
    database: bool
    redis: bool
    app_mode: str
    app_tier: str = "free"
    testing_mode: bool = True
    demo_mode: bool
    integrations_missing: int


class QuotaOut(BaseModel):
    scope: str
    provider: str
    period: str
    unit: str
    cap: float
    used: float
    remaining: float


class FreeTierOut(BaseModel):
    app_tier: str
    testing_mode: bool
    quotas: list[QuotaOut]
    #: live balances read from the providers (None when unavailable)
    serper_credits_live: int | None = None
    apify_used_usd_live: float | None = None
    apify_limit_usd_live: float | None = None
    apify_cycle_end: str | None = None
    mailpit_messages: int | None = None
    mailpit_url: str = "http://localhost:8025"


class BusinessOut(ORM):
    id: uuid.UUID
    name: str
    domain: str | None
    country: str | None
    language: str | None
    timezone: str | None
    industry: str | None
    address: str | None
    phone: str | None
    has_website: bool | None
    status: str
    cooldown_until: datetime | None
    registry_number: str | None
    registry_source: str | None


class LeadRow(ORM):
    id: uuid.UUID
    title: str | None
    business_name: str | None = None
    source: str
    source_url: str
    trigger_type: str
    service_needed: list[str]
    intent_score: int | None
    status: str
    status_reason: str | None
    country: str | None
    language: str | None
    timezone: str | None
    is_demo: bool = False
    created_at: datetime
    updated_at: datetime


class LeadPage(BaseModel):
    items: list[LeadRow]
    total: int
    page: int
    page_size: int


class EvidenceOut(ORM):
    id: uuid.UUID
    kind: str
    url: str | None
    file_path: str | None
    excerpt: str | None
    data: dict[str, Any]
    captured_at: datetime


class ContactOut(ORM):
    id: uuid.UUID
    type: str
    value: str
    source_url: str
    verification_status: str
    verified_at: datetime | None


class AgentRunOut(ORM):
    id: uuid.UUID
    agent: str
    lead_id: uuid.UUID | None
    status: str
    attempts: int
    error: str | None
    output: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
    cost_usd: float


class PolicyDecisionOut(ORM):
    id: int
    check: str
    action: str
    allowed: bool
    reason: str
    details: dict[str, Any]
    created_at: datetime


class ActivityOut(ORM):
    id: int
    kind: str
    agent: str | None
    lead_id: uuid.UUID | None
    message: str
    data: dict[str, Any]
    created_at: datetime


class LeadDetail(BaseModel):
    lead: LeadRow
    raw_text: str | None
    pipeline_state: dict[str, Any]
    business: BusinessOut | None
    evidence: list[EvidenceOut]
    brief: dict[str, Any] | None
    contacts: list[ContactOut]
    audit: dict[str, Any] | None
    logos: list[dict[str, Any]]
    preview: dict[str, Any] | None
    proposals: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    replies: list[dict[str, Any]]
    deal: dict[str, Any] | None
    agent_runs: list[AgentRunOut]
    policy_decisions: list[PolicyDecisionOut]
    activity: list[ActivityOut]


class StageCount(BaseModel):
    key: str
    label: str
    current: int
    reached: int


class AgentStatus(BaseModel):
    agent: str
    state: str  # running | idle | failed | needs_api_key | paused_budget
    last_run_at: datetime | None
    runs_24h: int
    failures_24h: int
    last_error: str | None
    cost_today_usd: float


class BudgetOut(BaseModel):
    spent_today: float
    spent_month: float
    daily_cap: float
    monthly_cap: float
    paused: bool


class DeliverabilityOut(BaseModel):
    sent_30d: int
    bounced_30d: int
    complained_30d: int
    bounce_rate: float
    complaint_rate: float
    replies_7d: int
    positive_replies_7d: int


class OverviewOut(BaseModel):
    stages: list[StageCount]
    stopped: dict[str, int]
    agents: list[AgentStatus]
    budget: BudgetOut
    deliverability: DeliverabilityOut
    leads_today: int
    leads_7d: int
    autonomy_stage: int
    awaiting_approval: int
    inbox_count: int


class SourceOut(BaseModel):
    key: str
    label: str
    description: str
    constraints: str
    integration: IntegrationOut | None
    enabled: bool
    schedule_minutes: int
    config: dict[str, Any]
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    leads_found_total: int
    leads_7d: int
    runs: list[dict[str, Any]]


class SourcePatch(BaseModel):
    enabled: bool | None = None
    schedule_minutes: int | None = Field(default=None, ge=15, le=10080)
    config: dict[str, Any] | None = None


class SettingsOut(BaseModel):
    runtime: dict[str, Any]
    integrations: list[IntegrationOut]
    country_rules: dict[str, Any]
    pricing: list[dict[str, Any]]
    sender_identity: dict[str, str]
    app_mode: str
    demo_mode: bool


class SettingsPatch(BaseModel):
    changes: dict[str, Any]
    reason: str | None = None


class CostPoint(BaseModel):
    day: str
    amount_usd: float


class CostsOut(BaseModel):
    daily: list[CostPoint]
    by_provider: dict[str, float]
    by_agent: dict[str, float]
    budget: BudgetOut


class SearchHit(BaseModel):
    kind: str
    id: str
    title: str
    subtitle: str | None = None
    href: str


class EvidenceRef(BaseModel):
    id: str
    kind: str
    url: str | None
    file_path: str | None


class AuditRow(BaseModel):
    lead_id: uuid.UUID
    business_name: str | None
    url: str
    scores: dict[str, float | None]
    severity_counts: dict[str, int]
    desktop_path: str | None
    report_pdf_path: str | None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditRow]
    total: int


class AuditDetail(BaseModel):
    lead_id: uuid.UUID
    business_name: str | None
    url: str
    scores: dict[str, float | None]
    findings: list[dict[str, Any]]
    severity_counts: dict[str, int]
    competitor_comparison: list[dict[str, Any]]
    before_after_summary: str | None
    report_pdf_path: str | None
    desktop_path: str | None
    mobile_path: str | None
    evidence: dict[str, EvidenceRef]
    created_at: datetime
