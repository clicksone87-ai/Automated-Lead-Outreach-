"""Agent run bookkeeping: `agent_runs` rows, cost totals, activity events, skip-if-done."""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.db.models import AgentRun, Cost, Lead, LeadStatus, RunStatus
from app.orchestrator.agents import AgentContext, AgentOutcome, LeadAgent
from app.orchestrator.budget import BudgetExceededError
from app.services import activity

logger = logging.getLogger(__name__)


class TransientAgentError(Exception):
    """Raise (or wrap) for failures worth retrying with backoff (network, 5xx, timeouts)."""


async def last_success(session: AsyncSession, agent: str, lead_id: uuid.UUID) -> AgentRun | None:
    return (
        await session.execute(
            select(AgentRun)
            .where(
                AgentRun.agent == agent,
                AgentRun.lead_id == lead_id,
                AgentRun.status == RunStatus.SUCCEEDED,
            )
            .order_by(AgentRun.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def execute(
    ctx: AgentContext, agent: LeadAgent, lead_id: uuid.UUID, *, force: bool = False
) -> AgentOutcome:
    """Run one agent for one lead, recording everything. Never raises for expected stops."""
    session = ctx.session
    if not force:
        done = await last_success(session, agent.name, lead_id)
        if done is not None:
            return AgentOutcome(
                proceed=bool(done.output.get("proceed", True)),
                reason="already completed",
                output=done.output,
            )

    run = AgentRun(
        agent=agent.name,
        lead_id=lead_id,
        status=RunStatus.RUNNING,
        attempts=1,
        started_at=datetime.now(UTC),
    )
    previous_attempts = (
        await session.execute(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.agent == agent.name, AgentRun.lead_id == lead_id)
        )
    ).scalar_one()
    run.attempts = int(previous_attempts) + 1
    session.add(run)
    await session.flush()
    await activity.emit(
        session, "agent_started", f"{agent.name} started", lead_id=lead_id, agent=agent.name
    )
    started = run.started_at

    async def finish(status: RunStatus, outcome: AgentOutcome | None, error: str | None) -> None:
        run.status = status
        run.error = error
        run.finished_at = datetime.now(UTC)
        run.output = (
            {"proceed": outcome.proceed, "reason": outcome.reason, **outcome.output}
            if outcome
            else {}
        )
        run.cost_usd = float(
            (
                await session.execute(
                    select(func.coalesce(func.sum(Cost.amount_usd), 0)).where(
                        Cost.agent == agent.name,
                        Cost.lead_id == lead_id,
                        Cost.created_at >= started,
                    )
                )
            ).scalar_one()
        )
        await session.flush()

    lead = await session.get(Lead, lead_id)
    try:
        async with session.begin_nested():
            outcome = await agent.run(ctx, lead_id)
    except BudgetExceededError as exc:
        outcome = AgentOutcome(proceed=False, status=LeadStatus.PAUSED_BUDGET, reason=str(exc))
        await finish(RunStatus.SKIPPED, outcome, str(exc))
    except AdapterUnavailableError as exc:
        outcome = AgentOutcome(
            proceed=False, reason=f"needs_api_key: {exc}", output={"needs_api_key": exc.integration}
        )
        await finish(RunStatus.SKIPPED, outcome, str(exc))
    except TransientAgentError as exc:
        await finish(RunStatus.RETRYING, None, str(exc))
        await activity.emit(
            session, "agent_retrying", f"{agent.name}: {exc}", lead_id=lead_id, agent=agent.name
        )
        await session.commit()  # keep the failure record; the caller's scope will roll back
        raise
    except Exception as exc:
        logger.exception("agent %s failed for lead %s", agent.name, lead_id)
        await finish(RunStatus.FAILED, None, "".join(traceback.format_exception(exc))[-4000:])
        await activity.emit(
            session,
            "agent_failed",
            f"{agent.name} failed: {exc}",
            lead_id=lead_id,
            agent=agent.name,
        )
        await session.commit()
        raise
    else:
        await finish(RunStatus.SUCCEEDED, outcome, None)

    if lead is not None and outcome.status:
        lead.status = outcome.status
        lead.status_reason = outcome.reason[:2000] if outcome.reason else None
    await activity.emit(
        session,
        "agent_finished",
        f"{agent.name}: {outcome.reason or ('continue' if outcome.proceed else 'stop')}",
        lead_id=lead_id,
        agent=agent.name,
        data={"proceed": outcome.proceed, "status": outcome.status},
    )
    await session.flush()
    return outcome
