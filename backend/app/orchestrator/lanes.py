"""Fast and heavy lanes.

Fast lane, every lead (target < 3 min): prefilter → batch LLM scoring → requirements →
contact → light audit → draft (an email, or Track B manual drafts). Leads are processed in
batches; independent leads run side by side, every step has a time limit, and a lead that hits
a free-tier cap or a transient error is deferred to the sweep instead of blocking the batch.

Heavy lane, top leads per day or on request (target < 10 min): full audit and branding side by
side → website preview → quality check → proposal redrafted with the assets, unless a human
already approved or sent the fast-lane draft. High-intent leads are offered to it as soon as
their fast lane ends, and their fast draft waits for a human instead of auto-sending. Heavy
work runs on its own queue and worker, so it never delays fast-lane leads.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters, get_adapters
from app.agents.requirements.batch import classify_batch
from app.config import Settings, get_settings
from app.db.models import (
    AgentRun,
    ApprovalStatus,
    Brief,
    Business,
    Lead,
    LeadStatus,
    Message,
    Proposal,
    RunStatus,
    TriggerType,
)
from app.db.session import session_scope
from app.orchestrator import pipeline
from app.orchestrator.agents import PIPELINE_ORDER, load_agents
from app.orchestrator.budget import BudgetExceededError
from app.orchestrator.errors import TransientAgentError
from app.orchestrator.pipeline import PipelineState
from app.orchestrator.runs import last_success
from app.orchestrator.steps import (
    FAST_LANE,
    FAST_TIMEOUTS,
    FULL,
    HEAVY_GRAPH,
    HEAVY_LANE,
    HEAVY_STEPS,
    HEAVY_TIMEOUTS,
)
from app.services import runtime_settings

__all__ = ["FAST_LANE", "FULL", "HEAVY_GRAPH", "HEAVY_LANE", "HEAVY_STEPS"]

logger = logging.getLogger(__name__)

FAST_STEPS = FAST_LANE
RETRY_AFTER = timedelta(minutes=10)
SCORING_TIMEOUT_S = 300  # one batch call, including a split retry and model fallbacks
PAUSED_RETRY = timedelta(hours=1)
STUCK_AFTER = timedelta(minutes=30)
HEAVY_STALE = timedelta(hours=2)  # a heavy job older than this died with its worker
STALE_AFTER = HEAVY_STALE
#: "High intent" tier on the dashboard (families.ts): these leads go to the heavy lane first.
HEAVY_MIN_SCORE = 75
STATE_KEY = "heavy"  # lead.pipeline_state["heavy"] = {"status", "reason", "at", "requested"}
ACTIVE = ("queued", "running")


def chunks(items: Sequence[uuid.UUID], size: int) -> Iterable[list[uuid.UUID]]:
    for i in range(0, len(items), max(1, size)):
        yield list(items[i : i + size])


async def _proposal_needs_assets(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """Heavy lane: redraft the proposal so it carries the assets, but only a fast-lane draft
    that is still waiting for a human (not approved, not sent, no assets yet). The Proposal
    agent marks the replaced draft as superseded."""
    latest = (
        await session.execute(
            select(Proposal)
            .where(Proposal.lead_id == lead_id)
            .order_by(Proposal.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        latest is None
        or latest.attachments
        or latest.approval_status not in (ApprovalStatus.PENDING, ApprovalStatus.DRAFT)
    ):
        return False
    sent = (
        await session.execute(
            select(func.count()).select_from(Message).where(Message.proposal_id == latest.id)
        )
    ).scalar_one()
    return not sent


async def _qa_needs_rerun(session: AsyncSession, lead_id: uuid.UUID) -> bool:
    """A producer ran again after the last quality check (e.g. QA returned its work for a fix):
    the old verdict no longer applies, so the check runs again instead of being skipped."""
    qa = await last_success(session, "quality_check", lead_id)
    if qa is None or qa.finished_at is None:
        return False
    for producer in ("branding", "website_builder"):
        run = await last_success(session, producer, lead_id)
        if run is not None and run.finished_at is not None and run.finished_at > qa.finished_at:
            return True
    return False


HEAVY_FORCE = {"proposal": _proposal_needs_assets, "quality_check": _qa_needs_rerun}


# ── heavy-lane state on the lead (read by the dashboard) ────────────────────


def heavy_steps_in(brief: dict[str, Any] | None) -> list[str]:
    return [s for s in HEAVY_STEPS if s in (brief or {}).get("agents_to_run", [])]


def state(lead: Lead) -> dict[str, Any]:
    value = (lead.pipeline_state or {}).get(STATE_KEY)
    return value if isinstance(value, dict) else {}


def set_state(lead: Lead, status: str, reason: str = "", **extra: Any) -> None:
    lead.pipeline_state = {  # new dict: JSONB change tracking
        **(lead.pipeline_state or {}),
        STATE_KEY: {
            **state(lead),
            **extra,
            "status": status,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        },
    }


def is_active(lead: Lead, now: datetime | None = None) -> bool:
    st = state(lead)
    if st.get("status") not in ACTIVE:
        return False
    try:
        at = datetime.fromisoformat(str(st.get("at")))
    except ValueError:
        return False
    return (now or datetime.now(UTC)) - at < STALE_AFTER


def qualifies(lead: Lead, brief: dict[str, Any] | None) -> bool:
    """High-intent leads whose brief needs heavy work, or a human asked for it."""
    if not heavy_steps_in(brief):
        return False
    return bool(state(lead).get("requested")) or (lead.intent_score or 0) >= HEAVY_MIN_SCORE


async def outstanding(session: AsyncSession, lead: Lead, brief: dict[str, Any] | None) -> bool:
    """True while heavy work this lead is due (high intent, picked or requested) has not
    finished. The Proposal agent then holds the fast-lane draft for a human instead of
    auto-approving it, so a top lead's email waits for its previews."""
    if not heavy_steps_in(brief):
        return False
    picked = lead.lane == "heavy" and heavy_unfinished(lead.lane_timings)
    if not (picked or qualifies(lead, brief)):
        return False
    for step in heavy_steps_in(brief):
        if await last_success(session, step, lead.id) is None:
            return True
    return False


def _heavy_status(entry: dict[str, Any]) -> tuple[str, str]:
    if entry.get("finished"):
        stop = entry.get("stopped_at")
        if stop == "error":
            return "failed", str(entry.get("error") or "")
        return ("done", "") if stop in (None, "proposal", "outreach") else ("stopped", str(stop))
    if entry.get("deferred"):
        return "queued", str(entry["deferred"])
    if entry.get("started"):
        return "running", ""
    return "queued", str(entry.get("requested_by") or "")


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
            if values.get("started") and entry.get("queued"):
                entry["queue_wait_s"] = round(
                    (
                        datetime.fromisoformat(entry["started"])
                        - datetime.fromisoformat(entry["queued"])
                    ).total_seconds(),
                    1,
                )
            if values.get("finished") and entry.get("started"):
                entry["seconds"] = round(
                    (
                        datetime.fromisoformat(entry["finished"])
                        - datetime.fromisoformat(entry["started"])
                    ).total_seconds(),
                    1,
                )
            lead.lane_timings = {**timings, lane: entry}
            if lane == "heavy":
                status, reason = _heavy_status(entry)
                requested = entry.get("requested_by") == "dashboard" or state(lead).get("requested")
                set_state(lead, status, reason, requested=bool(requested))


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


def needs_fast(status: str, timings: dict[str, Any] | None) -> bool:
    f = (timings or {}).get("fast") or {}
    return (
        not f.get("finished")
        or bool(f.get("deferred") or f.get("retry_after"))
        or status == LeadStatus.PAUSED_BUDGET
    )


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
        rows = (
            await s.execute(
                select(Lead.id, Lead.status, Lead.lane_timings).where(Lead.id.in_(list(lead_ids)))
            )
        ).all()
    # A retried job (worker restart) must not redo finished leads or overwrite their timings.
    todo = {lid for lid, status, timings in rows if needs_fast(status, timings)}
    ids = [lid for lid in dict.fromkeys(lead_ids) if lid in todo]
    await mark(ids, "fast", started=_now(), deferred=None, retry_after=None)
    agents = load_agents()

    # 1. Prefilter: plain rules, one cheap step per lead.
    survivors: list[uuid.UUID] = []
    for lead_id in ids:
        try:
            result = await pipeline.run_steps(
                lead_id, FAST_STEPS[:1], adapters, timeouts=FAST_TIMEOUTS
            )
        except TransientAgentError as exc:
            await _defer([lead_id], "fast", str(exc))
            continue
        if result.get("stopped_at"):
            await _finish(lead_id, "fast", result)
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
                result = await pipeline.run_steps(
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
            await _finish(lead_id, "fast", result)
            results["finished"] += 1

    await asyncio.gather(*(one(lead_id) for lead_id in survivors))
    promoted = await promote_high_intent(survivors)
    return {"leads": len(ids), "past_prefilter": len(survivors), "heavy": promoted, **results}


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
                await asyncio.wait_for(classify_batch(s, adapters, rows), SCORING_TIMEOUT_S)
        except BudgetExceededError as exc:  # free-tier cap: try again after it resets
            await _defer(part, "fast", f"scoring paused: {exc}", retry_in=PAUSED_RETRY)
            keep = [x for x in keep if x not in part]
        except (TransientAgentError, TimeoutError) as exc:
            await _defer(part, "fast", f"scoring retry: {exc or 'timed out'}")
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
        result = await pipeline.run_steps(
            lead_id, HEAVY_GRAPH, adapters, timeouts=HEAVY_TIMEOUTS, force_hooks=HEAVY_FORCE
        )
    except TransientAgentError as exc:
        await _defer([lead_id], "heavy", str(exc))
        raise
    except Exception as exc:
        # Recorded as finished-with-error so the lead is never stuck as "being generated".
        await mark([lead_id], "heavy", finished=_now(), stopped_at="error", error=str(exc)[:300])
        raise
    await _finish(lead_id, "heavy", result)
    return result


HEAVY_AGENTS = {"audit", "branding", "website_builder"}
EXTRA_ON_REQUEST = ("branding", "website_builder", "quality_check")
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


def gemini_day_start(now: datetime) -> datetime:
    """Gemini's free-tier daily quota resets at midnight Pacific time."""
    pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
    return pacific.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def gemini_daily_limit_hit(session: AsyncSession, now: datetime | None = None) -> bool:
    """Google's own daily request limit was hit since its last reset (logo models have a much
    smaller daily allowance than the text model): heavy-lane runs would stop at branding."""
    since = gemini_day_start(now or datetime.now(UTC))
    hit = (
        await session.execute(
            select(AgentRun.id)
            .where(
                AgentRun.status == RunStatus.SKIPPED,
                AgentRun.error.like("%gemini:daily(server)%"),
                AgentRun.finished_at >= since,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return hit is not None


async def pick_for_heavy(session: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Today's best leads that finished the fast lane, up to the daily heavy-lane limit:
    a verified email first, then intent, then the newest. Nothing is picked while Gemini's own
    daily limit is used up (a picked lead would just stop at branding and waste its slot)."""
    now = now or datetime.now(UTC)
    if await gemini_daily_limit_hit(session, now):
        return []
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


async def promote_high_intent(lead_ids: Sequence[uuid.UUID]) -> int:
    """Right after their fast lane: high-intent leads still due heavy work go to the heavy
    lane at once, within today's heavy-lane limit (the 15-minute pick fills the rest)."""
    if not lead_ids:
        return 0
    async with session_scope() as s:
        rs = await runtime_settings.load(s)
        slots = await heavy_slots_left(s, rs.heavy_lane_daily_limit)
        if slots <= 0 or await gemini_daily_limit_hit(s):
            return 0
        rows = (
            await s.execute(
                select(Lead, Brief.data)
                .join(Brief, Brief.lead_id == Lead.id)
                .where(
                    Lead.id.in_(list(lead_ids)),
                    or_(Lead.lane.is_(None), Lead.lane == "fast"),
                    Lead.status.in_([str(x) for x in HEAVY_READY]),
                )
            )
        ).all()
        due = [
            lead.id
            for lead, brief in rows
            if qualifies(lead, brief) and await outstanding(s, lead, brief)
        ]
    return await send_to_heavy(due[:slots], requested_by="high_intent") if due else 0


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


async def request_assets(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    """The dashboard's "Generate assets" button. A person chose this lead, so the daily
    heavy-lane limit does not apply (free-tier caps still do), and a lead whose brief needs no
    logo or preview (e.g. SEO only) gets them added: logo, website, and an audit if it has a site."""
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
    if heavy_in_progress((lead.lane_timings or {}).get("heavy") or {}):
        raise AssetsRequestError("assets are already being generated")
    business = await session.get(Business, lead.business_id) if lead.business_id else None
    wanted = list(EXTRA_ON_REQUEST)
    if business is not None and (business.domain or business.has_website):
        wanted.insert(0, "audit")
    agents = list(brief.data.get("agents_to_run", []))
    agents += [step for step in wanted if step not in agents]
    brief.data = {**brief.data, "agents_to_run": [s for s in PIPELINE_ORDER if s in agents]}
    set_state(lead, "queued", "requested from the dashboard", requested=True)
    await session.flush()
    return lead


def heavy_in_progress(heavy: dict[str, Any], now: datetime | None = None) -> bool:
    """Queued or running, and recent enough that the job is still alive."""
    if not heavy.get("queued") or heavy.get("finished") or heavy.get("deferred"):
        return False
    mark_at = heavy.get("started") or heavy["queued"]
    return (now or datetime.now(UTC)) - datetime.fromisoformat(mark_at) < HEAVY_STALE


# ── resume ──────────────────────────────────────────────────────────────────


async def continue_lead(lead_id: uuid.UUID, adapters: Adapters | None = None) -> PipelineState:
    """Run or resume one lead's fast steps (finished ones are skipped, so this is how a lead
    continues after a human approval); unfinished heavy work is put back on its queue."""
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        lane = lead.lane if lead else None
        timings = dict(lead.lane_timings or {}) if lead else {}
    result = await pipeline.run_steps(lead_id, FAST_STEPS, adapters, timeouts=FAST_TIMEOUTS)
    if lane == "heavy" and heavy_unfinished(timings):
        # Heavy work belongs on the heavy queue (one job at a time), never inline here.
        from app.orchestrator.queue import enqueue_heavy  # noqa: PLC0415

        await enqueue_heavy(lead_id)
    return result


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
