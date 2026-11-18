"""Fast and heavy lanes.

Fast lane, every lead (target < 3 min): prefilter → batch LLM scoring → requirements →
contact → light audit → draft. Leads are processed in batches; independent leads run side by
side, every step has a time limit, and a lead that hits a free-tier cap or a transient error is
deferred to the sweep instead of blocking the batch.

Heavy lane, top leads per day or on request (target < 10 min): full audit and branding side by
side → website preview → quality check → proposal redrafted with the assets.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters, get_adapters
from app.agents.requirements.batch import classify_batch
from app.config import Settings, get_settings
from app.db.models import ApprovalStatus, Brief, Lead, LeadStatus, Proposal, TriggerType
from app.db.session import session_scope
from app.orchestrator import pipeline
from app.orchestrator.agents import load_agents
from app.orchestrator.budget import BudgetExceededError
from app.orchestrator.errors import TransientAgentError
from app.orchestrator.pipeline import PipelineState, Step
from app.services import runtime_settings

logger = logging.getLogger(__name__)

FAST_STEPS: tuple[Step, ...] = (
    "prefilter",
    "requirements",
    "contact_enrichment",
    "audit_light",
    "proposal",
    "outreach",
)
HEAVY_STEPS: tuple[Step, ...] = (
    ("audit", "branding"),
    "website_builder",
    "quality_check",
    "proposal",
    "outreach",
)
FAST_TIMEOUTS = {
    "prefilter": 30,
    "requirements": 120,
    "contact_enrichment": 90,
    "audit_light": 120,
    "proposal": 180,
    "outreach": 60,
}
HEAVY_TIMEOUTS = {
    "audit": 420,
    "branding": 480,
    "website_builder": 600,
    "quality_check": 180,
    "proposal": 180,
    "outreach": 60,
}
RETRY_AFTER = timedelta(minutes=10)
PAUSED_RETRY = timedelta(hours=1)
STUCK_AFTER = timedelta(minutes=30)


def chunks(items: Sequence[uuid.UUID], size: int) -> Iterable[list[uuid.UUID]]:
    for i in range(0, len(items), max(1, size)):
        yield list(items[i : i + size])


async def _proposal_needs_assets(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """Heavy lane: a proposal still waiting for a human is redrafted so it carries the assets."""
    latest = (
        await session.execute(
            select(Proposal)
            .where(Proposal.lead_id == lead_id)
            .order_by(Proposal.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return latest is not None and latest.approval_status in (
        ApprovalStatus.PENDING,
        ApprovalStatus.DRAFT,
    )


HEAVY_FORCE = {"proposal": _proposal_needs_assets}


# ── timing marks on the lead ────────────────────────────────────────────────


async def mark(lead_ids: Iterable[uuid.UUID], lane: str, **values: Any) -> None:
    """Merge values into `lead_timings[lane]` (one short transaction)."""
    ids = list(lead_ids)
    if not ids:
        return
    async with session_scope() as s:
        for lead in (await s.execute(select(Lead).where(Lead.id.in_(ids)))).scalars():
            timings = dict(lead.lane_timings or {})
            entry = {**timings.get(lane, {}), **values}
            if values.get("finished") and entry.get("started"):
                entry["seconds"] = round(
                    (
                        datetime.fromisoformat(entry["finished"])
                        - datetime.fromisoformat(entry["started"])
                    ).total_seconds(),
                    1,
                )
            lead.lane_timings = {**timings, lane: entry}


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _finish(lead_id: uuid.UUID, lane: str, state: PipelineState) -> None:
    await mark([lead_id], lane, finished=_now(), stopped_at=state.get("stopped_at"))


async def _defer(
    lead_ids: Iterable[uuid.UUID], lane: str, reason: str, *, retry_in: timedelta = RETRY_AFTER
) -> None:
    retry_after = (datetime.now(UTC) + retry_in).isoformat()
    await mark(lead_ids, lane, deferred=reason[:300], retry_after=retry_after, finished=None,
               seconds=None)  # fmt: skip


# ── fast lane ───────────────────────────────────────────────────────────────


async def run_fast_batch(
    lead_ids: Sequence[uuid.UUID],
    adapters: Adapters | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run a batch of leads through the fast lane."""
    adapters = adapters or get_adapters()
    settings = settings or get_settings()
    async with session_scope() as s:
        rs = await runtime_settings.load(s)
    ids = list(dict.fromkeys(lead_ids))
    await mark(ids, "fast", started=_now(), deferred=None, retry_after=None)
    agents = load_agents()

    # 1. Prefilter: plain rules, one cheap step per lead.
    survivors: list[uuid.UUID] = []
    for lead_id in ids:
        try:
            state = await pipeline.run_steps(
                lead_id, FAST_STEPS[:1], adapters, timeouts=FAST_TIMEOUTS
            )
        except TransientAgentError as exc:
            await _defer([lead_id], "fast", str(exc))
            continue
        if state.get("stopped_at"):
            await _finish(lead_id, "fast", state)
        else:
            survivors.append(lead_id)

    # 2. Batch scoring for explicit requests not classified yet.
    if survivors and "requirements" in agents:
        survivors = await _score(survivors, adapters, rs.scoring_batch_size)

    # 3. The remaining fast steps, independent leads side by side.
    sem = asyncio.Semaphore(rs.fast_lane_concurrency)
    results: dict[str, int] = {"finished": 0, "deferred": 0, "failed": 0}

    async def one(lead_id: uuid.UUID) -> None:
        async with sem:
            try:
                state = await pipeline.run_steps(
                    lead_id, FAST_STEPS[1:], adapters, timeouts=FAST_TIMEOUTS
                )
            except TransientAgentError as exc:
                await _defer([lead_id], "fast", str(exc))
                results["deferred"] += 1
                return
            except Exception:
                logger.exception("fast lane failed for lead %s", lead_id)
                await mark([lead_id], "fast", finished=_now(), stopped_at="error")
                results["failed"] += 1
                return
            await _finish(lead_id, "fast", state)
            results["finished"] += 1

    await asyncio.gather(*(one(lead_id) for lead_id in survivors))
    return {"leads": len(ids), "past_prefilter": len(survivors), **results}


async def _score(lead_ids: list[uuid.UUID], adapters: Adapters, batch_size: int) -> list[uuid.UUID]:
    """Classify in batches. Leads that hit a cap or a transient error are deferred and dropped
    from this run; everything else continues (unresolved leads are classified one by one)."""
    keep = list(lead_ids)
    async with session_scope() as s:
        leads = list(
            (
                await s.execute(
                    select(Lead).where(
                        Lead.id.in_(lead_ids), Lead.trigger_type == TriggerType.EXPLICIT_REQUEST
                    )
                )
            ).scalars()
        )
    pending = [lead.id for lead in leads if not lead.pipeline_state.get("classification")]
    for part in chunks(pending, batch_size):
        try:
            async with session_scope() as s:
                rows = list((await s.execute(select(Lead).where(Lead.id.in_(part)))).scalars())
                await classify_batch(s, adapters, rows)
        except BudgetExceededError as exc:  # free-tier cap: try again after it resets
            await _defer(part, "fast", f"scoring paused: {exc}", retry_in=PAUSED_RETRY)
            keep = [x for x in keep if x not in part]
        except TransientAgentError as exc:
            await _defer(part, "fast", f"scoring retry: {exc}")
            keep = [x for x in keep if x not in part]
    return keep


async def enqueue_fast(lead_ids: Sequence[uuid.UUID], batch_size: int | None = None) -> int:
    """Queue leads for the fast lane in batches; returns the number of batches queued."""
    from app.orchestrator.queue import enqueue_fast_batch  # noqa: PLC0415

    ids = list(dict.fromkeys(lead_ids))
    if not ids:
        return 0
    if batch_size is None:
        async with session_scope() as s:
            batch_size = (await runtime_settings.load(s)).scoring_batch_size
    queued = 0
    for part in chunks(ids, batch_size):
        if await enqueue_fast_batch(part):
            await mark(part, "fast", queued=_now())
            queued += 1
    return queued


# ── heavy lane ──────────────────────────────────────────────────────────────


async def run_heavy(lead_id: uuid.UUID, adapters: Adapters | None = None) -> PipelineState:
    """Logos, website preview and the full audit for one lead, then a proposal with them."""
    await mark([lead_id], "heavy", started=_now(), deferred=None, retry_after=None)
    try:
        state = await pipeline.run_steps(
            lead_id, HEAVY_STEPS, adapters, timeouts=HEAVY_TIMEOUTS, force_hooks=HEAVY_FORCE
        )
    except TransientAgentError as exc:
        await _defer([lead_id], "heavy", str(exc))
        raise
    await _finish(lead_id, "heavy", state)
    return state


HEAVY_AGENTS = {"audit", "branding", "website_builder"}
HEAVY_READY = (LeadStatus.AWAITING_APPROVAL, LeadStatus.APPROVED)
GEMINI_PER_HEAVY_LEAD = 8  # logos (3 + repairs), site copy, vision review, proposal


class AssetsRequestError(Exception):
    pass


def _heavy_work(brief: dict[str, Any] | None) -> bool:
    return bool(HEAVY_AGENTS & set((brief or {}).get("agents_to_run", [])))


async def heavy_slots_left(session: AsyncSession, daily_limit: int) -> int:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.count()).where(
                Lead.lane_timings["heavy"]["queued"].astext >= today.isoformat()
            )
        )
    ).scalar_one()
    return max(0, daily_limit - int(used))


async def pick_for_heavy(session: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Today's best leads that finished the fast lane, up to the daily heavy-lane limit:
    a verified email first, then intent, then the newest."""
    now = now or datetime.now(UTC)
    rs = await runtime_settings.load(session)
    slots = await heavy_slots_left(session, rs.heavy_lane_daily_limit)
    if slots <= 0:
        return []
    since = (now - timedelta(days=1)).isoformat()
    rows = (
        await session.execute(
            select(Lead, Brief.data)
            .join(Brief, Brief.lead_id == Lead.id)
            .where(
                or_(Lead.lane.is_(None), Lead.lane == "fast"),
                Lead.status.in_([str(x) for x in HEAVY_READY]),
                Lead.intent_score >= rs.heavy_lane_min_score,
                Lead.lane_timings["fast"]["finished"].astext >= since,
            )
        )
    ).all()
    candidates = [lead for lead, brief in rows if _heavy_work(brief)]
    candidates.sort(
        key=lambda lead: (
            lead.pipeline_state.get("contact_channel") == "email",
            lead.intent_score or 0,
            lead.created_at,
        ),
        reverse=True,
    )
    return [lead.id for lead in candidates[:slots]]


async def send_to_heavy(lead_ids: Sequence[uuid.UUID], *, requested_by: str) -> int:
    from app.orchestrator.queue import enqueue_heavy  # noqa: PLC0415

    queued = 0
    for lead_id in lead_ids:
        async with session_scope() as s:
            lead = await s.get(Lead, lead_id)
            if lead is None:
                continue
            lead.lane = "heavy"
        if await enqueue_heavy(lead_id):
            await mark([lead_id], "heavy", queued=_now(), requested_by=requested_by,
                       started=None, finished=None, seconds=None)  # fmt: skip
            queued += 1
    return queued


async def request_assets(session: AsyncSession, lead_id: uuid.UUID) -> None:
    """Checks for the dashboard's "Generate assets" button. A person chose this lead, so the
    daily heavy-lane limit does not apply; free-tier caps still do."""
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise KeyError(lead_id)
    brief = await session.get(Brief, lead_id)
    if brief is None:
        raise AssetsRequestError("the lead has not been qualified yet")
    if not lead.pipeline_state.get("contact_id"):
        raise AssetsRequestError("no contact route: assets are only made for reachable leads")
    if lead.status in (LeadStatus.SUPPRESSED, LeadStatus.SENT, LeadStatus.REPLIED):
        raise AssetsRequestError(f"lead is {lead.status}")
    if not _heavy_work(brief.data):
        raise AssetsRequestError("this lead needs no logo, preview or audit (SEO only, no site)")
    heavy = (lead.lane_timings or {}).get("heavy") or {}
    if heavy.get("queued") and not heavy.get("finished") and not heavy.get("deferred"):
        raise AssetsRequestError("assets are already being generated")


# ── resume ──────────────────────────────────────────────────────────────────


async def continue_lead(lead_id: uuid.UUID, adapters: Adapters | None = None) -> PipelineState:
    """Run or resume one lead's fast steps (finished ones are skipped, so this is how a lead
    continues after a human approval); unfinished heavy work is put back on its queue."""
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        lane = lead.lane if lead else None
        timings = dict(lead.lane_timings or {}) if lead else {}
    state = await pipeline.run_steps(lead_id, FAST_STEPS, adapters, timeouts=FAST_TIMEOUTS)
    if lane == "heavy" and heavy_unfinished(timings):
        # Heavy work belongs on the heavy queue (one job at a time), never inline here.
        from app.orchestrator.queue import enqueue_heavy  # noqa: PLC0415

        await enqueue_heavy(lead_id)
    return state


def heavy_unfinished(timings: dict[str, Any] | None) -> bool:
    heavy = (timings or {}).get("heavy") or {}
    return bool(heavy.get("queued")) and not heavy.get("finished")


# ── sweep: leads that were never queued, got stuck or were deferred ─────────

SWEEP_STATUSES = (
    LeadStatus.DISCOVERED,
    LeadStatus.QUALIFIED,
    LeadStatus.CONTACT_FOUND,
    LeadStatus.AUDITED,
    LeadStatus.PAUSED_BUDGET,
)


def is_due(status: str, timings: dict[str, Any], updated_at: datetime, now: datetime) -> bool:
    f = (timings or {}).get("fast") or {}
    if f.get("retry_after"):
        return datetime.fromisoformat(f["retry_after"]) <= now
    if status == LeadStatus.PAUSED_BUDGET:
        return now - updated_at >= PAUSED_RETRY  # a free-tier cap may have reset
    if f.get("finished"):
        return False
    if f.get("started"):
        return now - datetime.fromisoformat(f["started"]) >= STUCK_AFTER  # worker died mid-run
    if f.get("queued"):
        return now - datetime.fromisoformat(f["queued"]) >= STUCK_AFTER  # job lost
    return status == LeadStatus.DISCOVERED


async def due_for_fast_lane(session: AsyncSession, *, limit: int = 200) -> list[uuid.UUID]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Lead.id, Lead.status, Lead.lane_timings, Lead.updated_at)
            .where(
                Lead.status.in_([str(x) for x in SWEEP_STATUSES]),
                or_(Lead.lane.is_(None), Lead.lane == "fast"),
                Lead.created_at >= now - timedelta(days=7),
                Lead.created_at <= now - timedelta(minutes=2),
            )
            .order_by(Lead.created_at)
            .limit(limit * 5)
        )
    ).all()
    due = [lid for lid, status, timings, updated in rows if is_due(status, timings, updated, now)]
    return due[:limit]
