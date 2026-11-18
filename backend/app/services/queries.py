"""Read models for the dashboard API (keeps business logic out of route handlers)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters
from app.agents.discovery.sources import SOURCES
from app.config import Settings, integration_status
from app.db.models import (
    ActivityEvent,
    AgentRun,
    ApprovalStatus,
    Audit,
    Brief,
    Business,
    Contact,
    Cost,
    Deal,
    DeliveryStatus,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
    Message,
    PolicyDecision,
    Preview,
    PricingPackage,
    Proposal,
    Reply,
    ReplyClass,
    RunStatus,
    Source,
    SourceRun,
)
from app.orchestrator import budget
from app.orchestrator.agents import PIPELINE_ORDER
from app.policy.country_rules import get_rules
from app.schemas import (
    ActivityOut,
    AgentRunOut,
    AgentStatus,
    AuditDetail,
    AuditPage,
    AuditRow,
    BudgetOut,
    BusinessOut,
    ContactOut,
    CostPoint,
    CostsOut,
    DeliverabilityOut,
    EvidenceOut,
    EvidenceRef,
    FreeTierOut,
    GalleryItem,
    IntegrationOut,
    LeadDetail,
    LeadPage,
    LeadRow,
    ManualRunOut,
    OverviewOut,
    PolicyDecisionOut,
    QuotaOut,
    SearchHit,
    SettingsOut,
    SourceOut,
    StageCount,
)
from app.services import manual_runs, runtime_settings

STAGES: list[tuple[str, str, set[str]]] = [
    ("discovery", "Discovery", {LeadStatus.DISCOVERED}),
    ("requirements", "Requirements", {LeadStatus.QUALIFIED}),
    ("contact", "Contact", {LeadStatus.CONTACT_FOUND}),
    ("audit", "Audit", {LeadStatus.AUDITED}),
    ("branding", "Branding", {LeadStatus.BRANDED}),
    ("builder", "Builder", {LeadStatus.BUILT}),
    ("qa", "QA", {LeadStatus.QA_PASSED, LeadStatus.NEEDS_REVIEW}),
    ("proposal", "Proposal", {LeadStatus.AWAITING_APPROVAL, LeadStatus.APPROVED}),
    (
        "outreach",
        "Outreach",
        {LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.WON, LeadStatus.LOST},
    ),
]
STOPPED = {
    LeadStatus.REJECTED_LOW_INTENT,
    LeadStatus.PARKED_LANGUAGE,
    LeadStatus.DUPLICATE,
    LeadStatus.COOLDOWN,
    LeadStatus.SUPPRESSED,
    LeadStatus.NO_CONTACT_ROUTE,
    LeadStatus.REJECTED_BY_HUMAN,
    LeadStatus.FAILED,
    LeadStatus.PAUSED_BUDGET,
    LeadStatus.CLOSED,
}
AGENTS = ("discovery", *PIPELINE_ORDER)


def integrations_out(settings: Settings, adapters: Adapters) -> list[IntegrationOut]:
    return [
        IntegrationOut(
            key=s.key,
            label=s.label,
            state="mock" if s.key in adapters.mocks_in_use else s.state,
            env_vars=list(s.env_vars),
            paid=s.paid,
            signup_url=s.signup_url,
            note=s.note,
            mock_in_use=s.key in adapters.mocks_in_use,
        )
        for s in integration_status(settings).values()
    ]


def _is_demo(lead: Lead) -> bool:
    return lead.source.startswith("mock") or bool(lead.pipeline_state.get("mock_adapters_used"))


def lead_row(lead: Lead, business_name: str | None) -> LeadRow:
    row = LeadRow.model_validate(lead)
    row.business_name = business_name
    row.is_demo = _is_demo(lead)
    return row


async def budget_out(session: AsyncSession) -> BudgetOut:
    snap = await budget.snapshot(session)
    return BudgetOut(
        spent_today=round(snap.spent_today, 4),
        spent_month=round(snap.spent_month, 4),
        daily_cap=snap.daily_cap,
        monthly_cap=snap.monthly_cap,
        paused=snap.paused,
    )


async def deliverability(session: AsyncSession) -> DeliverabilityOut:
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    row = (
        await session.execute(
            select(
                func.count().filter(Message.sent_at.is_not(None)),
                func.count().filter(Message.delivery_status == DeliveryStatus.BOUNCED),
                func.count().filter(Message.complained.is_(True)),
            ).where(Message.created_at >= since)
        )
    ).one()
    sent, bounced, complained = int(row[0]), int(row[1]), int(row[2])
    week = now - timedelta(days=7)
    replies = (
        await session.execute(
            select(
                func.count(), func.count().filter(Reply.classification == ReplyClass.POSITIVE)
            ).where(Reply.received_at >= week)
        )
    ).one()
    return DeliverabilityOut(
        sent_30d=sent,
        bounced_30d=bounced,
        complained_30d=complained,
        bounce_rate=round(bounced / sent, 4) if sent else 0.0,
        complaint_rate=round(complained / sent, 5) if sent else 0.0,
        replies_7d=int(replies[0]),
        positive_replies_7d=int(replies[1]),
    )


async def agent_statuses(session: AsyncSession) -> list[AgentStatus]:
    now = datetime.now(UTC)
    day_ago = now - timedelta(hours=24)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        r.agent: r
        for r in (
            await session.execute(
                select(
                    AgentRun.agent,
                    func.max(AgentRun.started_at).label("last"),
                    func.count().filter(AgentRun.created_at >= day_ago).label("runs"),
                    func.count()
                    .filter(
                        and_(AgentRun.created_at >= day_ago, AgentRun.status == RunStatus.FAILED)
                    )
                    .label("fails"),
                    func.count().filter(AgentRun.status == RunStatus.RUNNING).label("running"),
                ).group_by(AgentRun.agent)
            )
        ).all()
    }
    costs = dict(
        (
            await session.execute(
                select(Cost.agent, func.sum(Cost.amount_usd))
                .where(Cost.created_at >= today)
                .group_by(Cost.agent)
            )
        ).all()
    )
    last_errors: dict[str, tuple[str | None, str]] = {}
    for agent in AGENTS:
        last = (
            await session.execute(
                select(AgentRun.error, AgentRun.status)
                .where(AgentRun.agent == agent)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
        ).first()
        if last:
            last_errors[agent] = (last[0], last[1])
    discovery_last = (await session.execute(select(func.max(SourceRun.started_at)))).scalar()
    discovery_fails = (
        await session.execute(
            select(func.count())
            .select_from(SourceRun)
            .where(SourceRun.started_at >= day_ago, SourceRun.status == RunStatus.FAILED)
        )
    ).scalar_one()
    discovery_runs = (
        await session.execute(
            select(func.count()).select_from(SourceRun).where(SourceRun.started_at >= day_ago)
        )
    ).scalar_one()
    discovery_running = (
        await session.execute(
            select(func.count()).select_from(SourceRun).where(SourceRun.status == RunStatus.RUNNING)
        )
    ).scalar_one()

    out: list[AgentStatus] = []
    for agent in AGENTS:
        if agent == "discovery":
            state = "running" if discovery_running else ("failed" if discovery_fails else "idle")
            out.append(
                AgentStatus(
                    agent=agent,
                    state=state,
                    last_run_at=discovery_last,
                    runs_24h=int(discovery_runs),
                    failures_24h=int(discovery_fails),
                    last_error=None,
                    cost_today_usd=float(costs.get(agent, 0) or 0),
                )
            )
            continue
        s = stats.get(agent)
        err, last_status = last_errors.get(agent, (None, ""))
        if s and s.running:
            state = "running"
        elif last_status == RunStatus.FAILED:
            state = "failed"
        elif (err and err.startswith("needs_api_key")) or (err and "not configured" in err):
            state = "needs_api_key"
        elif err and "budget cap" in err:
            state = "paused_budget"
        else:
            state = "idle"
        out.append(
            AgentStatus(
                agent=agent,
                state=state,
                last_run_at=s.last if s else None,
                runs_24h=int(s.runs) if s else 0,
                failures_24h=int(s.fails) if s else 0,
                last_error=err if last_status == RunStatus.FAILED else None,
                cost_today_usd=round(float(costs.get(agent, 0) or 0), 4),
            )
        )
    return out


async def overview(session: AsyncSession) -> OverviewOut:
    counts = dict(
        (await session.execute(select(Lead.status, func.count()).group_by(Lead.status))).all()
    )
    stages: list[StageCount] = []
    cumulative = 0
    for key, label, statuses in reversed(STAGES):
        current = sum(int(counts.get(s, 0)) for s in statuses)
        cumulative += current
        stages.append(StageCount(key=key, label=label, current=current, reached=cumulative))
    stages.reverse()
    # "reached" for discovery = every lead ever found (stopped ones included)
    total = sum(int(v) for v in counts.values())
    stages[0].reached = total
    now = datetime.now(UTC)
    rs = await runtime_settings.load(session)
    leads_today = (
        await session.execute(
            select(func.count())
            .select_from(Lead)
            .where(Lead.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
        )
    ).scalar_one()
    leads_7d = (
        await session.execute(
            select(func.count()).select_from(Lead).where(Lead.created_at >= now - timedelta(days=7))
        )
    ).scalar_one()
    awaiting = (
        await session.execute(
            select(func.count())
            .select_from(Proposal)
            .where(Proposal.approval_status == ApprovalStatus.PENDING)
        )
    ).scalar_one()
    inbox = (
        await session.execute(
            select(func.count()).select_from(Reply).where(Reply.handed_to_inbox.is_(True))
        )
    ).scalar_one()
    return OverviewOut(
        stages=stages,
        stopped={str(s): int(counts[s]) for s in STOPPED if counts.get(s)},
        agents=await agent_statuses(session),
        budget=await budget_out(session),
        deliverability=await deliverability(session),
        leads_today=int(leads_today),
        leads_7d=int(leads_7d),
        autonomy_stage=rs.autonomy_stage,
        awaiting_approval=int(awaiting),
        inbox_count=int(inbox),
    )


def _lead_filters(
    stmt: Any,
    *,
    status: list[str] | None,
    stage: str | None,
    source: list[str] | None,
    country: list[str] | None,
    language: list[str] | None,
    service: list[str] | None,
    trigger: list[str] | None,
    min_score: int | None,
    max_score: int | None,
    unscored: str | None = None,
    q: str | None,
) -> Any:
    if status:
        stmt = stmt.where(Lead.status.in_(status))
    if stage:
        if stage == "stopped":
            stmt = stmt.where(Lead.status.in_([str(s) for s in STOPPED]))
        else:
            match = next((s for k, _, s in STAGES if k == stage), None)
            if match:
                stmt = stmt.where(Lead.status.in_([str(s) for s in match]))
    if source:
        stmt = stmt.where(Lead.source.in_(source))
    if country:
        stmt = stmt.where(Lead.country.in_(country))
    if language:
        stmt = stmt.where(Lead.language.in_(language))
    if service:
        stmt = stmt.where(Lead.service_needed.overlap(service))
    if trigger:
        stmt = stmt.where(Lead.trigger_type.in_(trigger))
    if unscored == "only":
        stmt = stmt.where(Lead.intent_score.is_(None))
    else:
        score_rules = []
        if min_score is not None:
            score_rules.append(Lead.intent_score >= min_score)
        if max_score is not None:
            score_rules.append(Lead.intent_score <= max_score)
        if score_rules:
            rule = and_(*score_rules)
            if unscored == "include":
                rule = or_(rule, Lead.intent_score.is_(None))
            stmt = stmt.where(rule)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.title.ilike(like),
                Business.name.ilike(like),
                Lead.raw_text.ilike(like),
                Business.domain.ilike(like),
            )
        )
    return stmt


SORTS = {
    "created_at": Lead.created_at,
    "updated_at": Lead.updated_at,
    "intent_score": Lead.intent_score,
    "status": Lead.status,
    "country": Lead.country,
}


async def list_leads(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 50,
    sort: str = "-created_at",
    **filters: Any,
) -> LeadPage:
    base = select(Lead, Business.name).outerjoin(Business, Lead.business_id == Business.id)
    base = _lead_filters(base, **filters)
    total = (
        await session.execute(select(func.count()).select_from(base.order_by(None).subquery()))
    ).scalar_one()
    col = SORTS.get(sort.lstrip("-"), Lead.created_at)
    order = col.desc().nulls_last() if sort.startswith("-") else col.asc().nulls_last()
    rows = (
        await session.execute(
            base.order_by(order, Lead.id).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return LeadPage(
        items=[lead_row(lead, name) for lead, name in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def _dump(obj: Any, *fields: str) -> dict[str, Any]:
    return {f: getattr(obj, f) for f in fields}


async def lead_detail(session: AsyncSession, lead_id: uuid.UUID) -> LeadDetail | None:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        return None
    business = await session.get(Business, lead.business_id) if lead.business_id else None

    async def all_(stmt: Select[Any]) -> list[Any]:
        return list((await session.execute(stmt)).scalars().all())

    evidence = await all_(
        select(Evidence).where(Evidence.lead_id == lead_id).order_by(Evidence.captured_at)
    )
    brief = await session.get(Brief, lead_id)
    contacts = (
        await all_(select(Contact).where(Contact.business_id == lead.business_id))
        if lead.business_id
        else []
    )
    audit_detail = await audit_view(session, lead_id)
    logos = await all_(
        select(LogoConcept).where(LogoConcept.lead_id == lead_id).order_by(LogoConcept.variant)
    )
    preview = (
        await session.execute(select(Preview).where(Preview.lead_id == lead_id))
    ).scalar_one_or_none()
    proposals = await all_(
        select(Proposal).where(Proposal.lead_id == lead_id).order_by(Proposal.version.desc())
    )
    proposal_ids = [p.id for p in proposals]
    messages = (
        await all_(
            select(Message)
            .where(Message.proposal_id.in_(proposal_ids))
            .order_by(Message.created_at)
        )
        if proposal_ids
        else []
    )
    message_ids = [m.id for m in messages]
    replies = (
        await all_(
            select(Reply).where(Reply.message_id.in_(message_ids)).order_by(Reply.received_at)
        )
        if message_ids
        else []
    )
    deal = (await session.execute(select(Deal).where(Deal.lead_id == lead_id))).scalar_one_or_none()
    runs = await all_(
        select(AgentRun).where(AgentRun.lead_id == lead_id).order_by(AgentRun.created_at)
    )
    decisions = await all_(
        select(PolicyDecision)
        .where(PolicyDecision.lead_id == lead_id)
        .order_by(PolicyDecision.created_at.desc())
        .limit(100)
    )
    events = await all_(
        select(ActivityEvent)
        .where(ActivityEvent.lead_id == lead_id)
        .order_by(ActivityEvent.created_at.desc())
        .limit(200)
    )

    return LeadDetail(
        lead=lead_row(lead, business.name if business else None),
        raw_text=lead.raw_text,
        pipeline_state=lead.pipeline_state,
        business=BusinessOut.model_validate(business) if business else None,
        evidence=[EvidenceOut.model_validate(e) for e in evidence],
        brief=brief.data if brief else None,
        contacts=[ContactOut.model_validate(c) for c in contacts],
        audit=audit_detail.model_dump(mode="json") if audit_detail else None,
        logos=[
            _dump(
                lg,
                "id",
                "variant",
                "svg_path",
                "png_path",
                "palette",
                "typography",
                "qa_status",
                "qa_report",
                "text",
                "language",
                "similarity",
                "attempts",
            )
            for lg in logos
        ],
        preview=_dump(
            preview,
            "id",
            "template_id",
            "preview_url",
            "screenshots",
            "test_results",
            "qa_status",
            "source_path",
            "mode",
            "language",
            "git_commit",
            "pdf_path",
            "qa_report",
            "built_at",
            "reused_content",
            "attempts",
        )
        if preview
        else None,
        proposals=[
            _dump(
                p,
                "id",
                "version",
                "language",
                "subject",
                "body_html",
                "body_text",
                "attachments",
                "claims",
                "approval_status",
                "approved_by",
                "approved_at",
                "rejection_reason",
                "created_at",
            )
            for p in proposals
        ],
        messages=[
            _dump(
                m,
                "id",
                "proposal_id",
                "to_email",
                "scheduled_for",
                "sent_at",
                "delivery_status",
                "bounce_type",
                "complained",
                "follow_up_number",
            )
            for m in messages
        ],
        replies=[
            _dump(
                r,
                "id",
                "message_id",
                "from_email",
                "subject",
                "received_at",
                "raw",
                "classification",
                "handed_to_inbox",
            )
            for r in replies
        ],
        deal=_dump(
            deal,
            "id",
            "quote",
            "contract_status",
            "payment_status",
            "delivery_status",
            "notes",
            "updated_at",
        )
        if deal
        else None,
        agent_runs=[AgentRunOut.model_validate(r) for r in runs],
        policy_decisions=[PolicyDecisionOut.model_validate(d) for d in decisions],
        activity=[ActivityOut.model_validate(e) for e in events],
    )


async def sources(session: AsyncSession, settings: Settings, adapters: Adapters) -> list[SourceOut]:
    integrations = {i.key: i for i in integrations_out(settings, adapters)}
    rows = {r.key: r for r in (await session.execute(select(Source))).scalars()}
    week = datetime.now(UTC) - timedelta(days=7)
    recent = dict(
        (
            await session.execute(
                select(Lead.source, func.count())
                .where(Lead.created_at >= week)
                .group_by(Lead.source)
            )
        ).all()
    )
    out = []
    for key, src in SOURCES.items():
        row = rows.get(key)
        runs = (
            (
                await session.execute(
                    select(SourceRun)
                    .where(SourceRun.source_key == key)
                    .order_by(SourceRun.started_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        cfg = {
            k: v
            for k, v in (row.config if row else src.default_config).items()
            if not k.startswith("_")
        }
        verdict = await manual_runs.evaluate(session, settings, adapters, key, live=False)
        out.append(
            SourceOut(
                key=key,
                label=src.label,
                description=src.description,
                constraints=src.constraints,
                integration=integrations.get(src.integration),
                enabled=bool(row and row.enabled),
                schedule_minutes=row.schedule_minutes if row else src.default_schedule_minutes,
                config=cfg,
                last_run_at=row.last_run_at if row else None,
                last_status=row.last_status if row else None,
                last_error=row.last_error if row else None,
                leads_found_total=row.leads_found_total if row else 0,
                leads_7d=int(recent.get(key, 0)),
                runs=[
                    _dump(
                        r,
                        "id",
                        "started_at",
                        "finished_at",
                        "status",
                        "items_seen",
                        "leads_created",
                        "error",
                    )
                    for r in runs
                ],
                manual_run=ManualRunOut(
                    limited=verdict.limited,
                    allowed=verdict.allowed,
                    reason=verdict.reason,
                    next_allowed_at=verdict.next_allowed_at,
                    usage=verdict.usage,
                ),
            )
        )
    return out


async def settings_view(
    session: AsyncSession, settings: Settings, adapters: Adapters
) -> SettingsOut:
    rs = await runtime_settings.load(session)
    rules = get_rules()
    pricing = (
        (await session.execute(select(PricingPackage).order_by(PricingPackage.sort_order)))
        .scalars()
        .all()
    )
    return SettingsOut(
        runtime=rs.model_dump(),
        integrations=integrations_out(settings, adapters),
        country_rules={
            code: r.model_dump() | {"can_draft": r.can_draft, "can_send": r.can_send}
            for code, r in rules.countries.items()
        },
        pricing=[
            _dump(
                p,
                "id",
                "service",
                "package",
                "description",
                "price_usd",
                "price_eur",
                "price_gbp",
                "sort_order",
            )
            for p in pricing
        ],
        sender_identity={
            "sender_name": settings.sender_name,
            "business_name": settings.sender_business_name,
            "postal_address": settings.sender_postal_address,
            "portfolio_url": settings.portfolio_url,
            "sending_domain": settings.sending_domain,
        },
        app_mode=str(settings.app_mode),
        demo_mode=settings.mocks_allowed,
    )


_LIVE_CACHE: dict[str, tuple[float, Any]] = {}


async def _cached(key: str, ttl: float, fn: Any) -> Any:
    import time  # noqa: PLC0415

    hit = _LIVE_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = await fn()
    _LIVE_CACHE[key] = (time.monotonic(), value)
    return value


async def free_tier(session: AsyncSession, settings: Settings, adapters: Adapters) -> FreeTierOut:
    """Free-tier usage vs caps + live provider balances (cached 60 s; no credit consumed)."""
    from app.orchestrator import quota  # noqa: PLC0415

    report = await quota.report(session, settings)
    serper = adapters.serper
    credits = await _cached("serper", 60, serper.credits) if serper else None
    apify_usage = (
        await _cached("apify", 60, adapters.apify.monthly_usage) if adapters.apify else None
    )
    info_fn = getattr(adapters.mailbox, "info", None)
    mail_info = await _cached("mailpit", 30, info_fn) if info_fn else None
    return FreeTierOut(
        app_tier=str(settings.app_tier),
        testing_mode=settings.testing_tier,
        quotas=[QuotaOut.model_validate(r) for r in report],
        serper_credits_live=credits,
        apify_used_usd_live=apify_usage["used_usd"] if apify_usage else None,
        apify_limit_usd_live=apify_usage["limit_usd"] if apify_usage else None,
        apify_cycle_end=apify_usage["cycle_end"] if apify_usage else None,
        mailpit_messages=int(mail_info.get("Messages", 0)) if mail_info else None,
    )


async def costs(session: AsyncSession, days: int = 30) -> CostsOut:
    since = datetime.now(UTC) - timedelta(days=days)
    day = func.date_trunc("day", Cost.created_at)
    daily = (
        await session.execute(
            select(day, func.sum(Cost.amount_usd))
            .where(Cost.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    by_provider = dict(
        (
            await session.execute(
                select(Cost.provider, func.sum(Cost.amount_usd))
                .where(Cost.created_at >= month)
                .group_by(Cost.provider)
            )
        ).all()
    )
    by_agent = dict(
        (
            await session.execute(
                select(Cost.agent, func.sum(Cost.amount_usd))
                .where(Cost.created_at >= month)
                .group_by(Cost.agent)
            )
        ).all()
    )
    return CostsOut(
        daily=[
            CostPoint(day=d.date().isoformat(), amount_usd=round(float(a), 4)) for d, a in daily
        ],
        by_provider={k: round(float(v), 4) for k, v in by_provider.items()},
        by_agent={k: round(float(v), 4) for k, v in by_agent.items()},
        budget=await budget_out(session),
    )


async def activity_since(
    session: AsyncSession, after_id: int = 0, limit: int = 100
) -> list[ActivityOut]:
    stmt = select(ActivityEvent).order_by(ActivityEvent.id.desc()).limit(limit)
    if after_id:
        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.id > after_id)
            .order_by(ActivityEvent.id)
            .limit(limit)
        )
    rows = (await session.execute(stmt)).scalars().all()
    events = [ActivityOut.model_validate(e) for e in rows]
    return events if after_id else list(reversed(events))


async def search(session: AsyncSession, q: str, limit: int = 8) -> list[SearchHit]:
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    rows = (
        await session.execute(
            select(Lead, Business.name)
            .outerjoin(Business, Lead.business_id == Business.id)
            .where(
                or_(Lead.title.ilike(like), Business.name.ilike(like), Business.domain.ilike(like))
            )
            .order_by(case((Business.name.ilike(f"{q}%"), 0), else_=1), Lead.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        SearchHit(
            kind="lead",
            id=str(lead.id),
            title=name or lead.title or "Lead",
            subtitle=f"{lead.status} · {lead.source} · {lead.country or '—'}",
            href=f"/leads/{lead.id}",
        )
        for lead, name in rows
    ]


SEVERITIES = ("critical", "high", "medium", "low")


def _severity_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITIES}


async def _evidence_refs(session: AsyncSession, ids: set[str]) -> dict[str, EvidenceRef]:
    valid = []
    for raw in ids:
        try:
            valid.append(uuid.UUID(raw))
        except (ValueError, TypeError):
            continue
    if not valid:
        return {}
    rows = (await session.execute(select(Evidence).where(Evidence.id.in_(valid)))).scalars()
    return {
        str(e.id): EvidenceRef(id=str(e.id), kind=e.kind, url=e.url, file_path=e.file_path)
        for e in rows
    }


async def audit_view(session: AsyncSession, lead_id: uuid.UUID) -> AuditDetail | None:
    row = (
        await session.execute(
            select(Audit, Business.name)
            .join(Lead, Lead.id == Audit.lead_id)
            .outerjoin(Business, Lead.business_id == Business.id)
            .where(Audit.lead_id == lead_id)
        )
    ).first()
    if row is None:
        return None
    audit, name = row
    findings = list(audit.findings or [])
    shots = audit.screenshots or {}
    ids = {f.get("evidence_id") for f in findings} | {shots.get("desktop"), shots.get("mobile")}
    refs = await _evidence_refs(session, {i for i in ids if i})
    return AuditDetail(
        lead_id=audit.lead_id,
        business_name=name,
        url=audit.url,
        scores=audit.scores or {},
        findings=findings,
        severity_counts=_severity_counts(findings),
        competitor_comparison=list(audit.competitor_comparison or []),
        before_after_summary=audit.before_after_summary,
        report_pdf_path=audit.report_pdf_path,
        desktop_path=refs[shots["desktop"]].file_path if shots.get("desktop") in refs else None,
        mobile_path=refs[shots["mobile"]].file_path if shots.get("mobile") in refs else None,
        evidence=refs,
        created_at=audit.created_at,
    )


async def list_audits(session: AsyncSession, *, page: int = 1, page_size: int = 24) -> AuditPage:
    total = (await session.execute(select(func.count()).select_from(Audit))).scalar_one()
    rows = (
        await session.execute(
            select(Audit, Business.name)
            .join(Lead, Lead.id == Audit.lead_id)
            .outerjoin(Business, Lead.business_id == Business.id)
            .order_by(Audit.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    desktop_ids = {(a.screenshots or {}).get("desktop") for a, _ in rows}
    refs = await _evidence_refs(session, {i for i in desktop_ids if i})
    items = []
    for audit, name in rows:
        desktop = (audit.screenshots or {}).get("desktop")
        items.append(
            AuditRow(
                lead_id=audit.lead_id,
                business_name=name,
                url=audit.url,
                scores=audit.scores or {},
                severity_counts=_severity_counts(list(audit.findings or [])),
                desktop_path=refs[desktop].file_path if desktop in refs else None,
                report_pdf_path=audit.report_pdf_path,
                created_at=audit.created_at,
            )
        )
    return AuditPage(items=items, total=int(total))


async def gallery(session: AsyncSession, limit: int = 60) -> list[GalleryItem]:
    """Leads with logo concepts and/or a website preview, newest first (Previews & Logos)."""
    lead_ids = (
        (await session.execute(select(LogoConcept.lead_id).union(select(Preview.lead_id))))
        .scalars()
        .all()
    )
    if not lead_ids:
        return []
    leads = (
        await session.execute(
            select(Lead, Business.name)
            .outerjoin(Business, Business.id == Lead.business_id)
            .where(Lead.id.in_(lead_ids))
            .order_by(Lead.updated_at.desc())
            .limit(limit)
        )
    ).all()
    out: list[GalleryItem] = []
    for lead, name in leads:
        logos = (
            (
                await session.execute(
                    select(LogoConcept)
                    .where(LogoConcept.lead_id == lead.id)
                    .order_by(LogoConcept.variant)
                )
            )
            .scalars()
            .all()
        )
        preview = (
            await session.execute(select(Preview).where(Preview.lead_id == lead.id))
        ).scalar_one_or_none()
        out.append(
            GalleryItem(
                lead_id=lead.id,
                business_name=name,
                status=lead.status,
                language=lead.language,
                logos=[
                    {
                        "id": str(lg.id),
                        "variant": lg.variant,
                        "png_path": lg.png_path,
                        "svg_path": lg.svg_path,
                        "concept_name": (lg.typography or {}).get("concept_name"),
                        "style": (lg.typography or {}).get("style"),
                        "qa_status": lg.qa_status,
                        "similarity": lg.similarity,
                    }
                    for lg in logos
                ],
                preview=(
                    {
                        "preview_url": preview.preview_url,
                        "screenshots": preview.screenshots,
                        "qa_status": preview.qa_status,
                        "mode": preview.mode,
                        "variant": (preview.content or {}).get("variant"),
                        "lighthouse": (preview.test_results or {}).get("lighthouse", {}),
                        "tests_passed": (preview.test_results or {}).get("passed"),
                        "pdf_path": preview.pdf_path,
                    }
                    if preview
                    else None
                ),
            )
        )
    return out
