"""Per-lead LangGraph workflow over an ordered list of steps.

Completed steps are skipped on re-entry, so re-invoking a graph resumes at the first unfinished
step. A step can be a group of agents that run side by side (each in its own session), every
step can have a time limit, and a hook can force a completed step to run again.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters, get_adapters
from app.config import Settings, get_settings
from app.db.models import AgentRun, RunStatus
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import (
    PIPELINE_ORDER,
    AgentContext,
    AgentOutcome,
    LeadAgent,
    load_agents,
)
from app.orchestrator.errors import TransientAgentError

logger = logging.getLogger(__name__)

Step = str | tuple[str, ...]  # a tuple runs its agents in parallel
ForceHook = Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]


class PipelineState(TypedDict, total=False):
    lead_id: str
    completed: list[str]
    stopped_at: str | None
    reason: str


class StepTimeoutError(TransientAgentError):
    """A step ran past its time limit; recorded as retrying and picked up again later."""


NodeFn = Callable[[PipelineState], Awaitable[dict[str, Any]]]


async def execute_step(
    agent: LeadAgent,
    lead_id: uuid.UUID,
    adapters: Adapters,
    settings: Settings,
    *,
    timeout_s: float | None = None,
    force_hook: ForceHook | None = None,
) -> AgentOutcome:
    """One agent for one lead in its own transaction, within an optional time limit."""
    started = datetime.now(UTC)
    try:
        async with session_scope() as session:
            ctx = AgentContext(session=session, adapters=adapters, settings=settings)
            force = bool(force_hook and await force_hook(session, lead_id))
            work = runs.execute(ctx, agent, lead_id, force=force)
            return await (asyncio.wait_for(work, timeout_s) if timeout_s else work)
    except TimeoutError as exc:
        reason = f"{agent.name} timed out after {timeout_s:.0f}s"
        async with session_scope() as session:
            session.add(
                AgentRun(agent=agent.name, lead_id=lead_id, status=RunStatus.RETRYING,
                         attempts=1, error=reason, started_at=started,
                         finished_at=datetime.now(UTC))
            )  # fmt: skip
        raise StepTimeoutError(reason) from exc


def _node(
    agents: Sequence[LeadAgent],
    adapters: Adapters,
    settings: Settings,
    timeouts: Mapping[str, float],
    force_hooks: Mapping[str, ForceHook],
) -> NodeFn:
    async def run_node(state: PipelineState) -> dict[str, Any]:
        lead_id = uuid.UUID(state["lead_id"])
        results = await asyncio.gather(
            *(
                execute_step(a, lead_id, adapters, settings, timeout_s=timeouts.get(a.name),
                             force_hook=force_hooks.get(a.name))
                for a in agents
            ),
            return_exceptions=True,
        )  # fmt: skip
        for result in results:  # let the whole group finish, then surface the first failure
            if isinstance(result, BaseException):
                raise result
        completed = [*state.get("completed", []), *(a.name for a in agents)]
        for agent, outcome in zip(agents, results, strict=True):
            assert isinstance(outcome, AgentOutcome)
            if not outcome.proceed:
                return {"completed": completed, "stopped_at": agent.name, "reason": outcome.reason}
        return {"completed": completed}

    return run_node


def build_graph(
    agents: dict[str, LeadAgent],
    adapters: Adapters,
    settings: Settings | None = None,
    *,
    steps: Sequence[Step] | None = None,
    timeouts: Mapping[str, float] | None = None,
    force_hooks: Mapping[str, ForceHook] | None = None,
) -> Any:
    """Compile a graph over `steps` (default: every registered agent in pipeline order).
    Steps whose agents are not registered are left out."""
    settings = settings or get_settings()
    wanted: Sequence[Step] = steps if steps is not None else PIPELINE_ORDER
    groups: list[tuple[str, list[LeadAgent]]] = []
    for step in wanted:
        names = step if isinstance(step, tuple) else (step,)
        present = [agents[n] for n in names if n in agents]
        if present:
            groups.append(("+".join(a.name for a in present), present))
    graph: Any = StateGraph(PipelineState)
    if not groups:
        graph.add_node("noop", _noop)
        graph.add_edge(START, "noop")
        graph.add_edge("noop", END)
        return graph.compile()
    for name, members in groups:
        graph.add_node(name, _node(members, adapters, settings, timeouts or {}, force_hooks or {}))
    order = [name for name, _ in groups]
    graph.add_edge(START, order[0])
    for current, nxt in zip(order, [*order[1:], None], strict=True):
        graph.add_conditional_edges(
            current,
            _router(nxt),
            {"next": nxt, "end": END} if nxt else {"end": END},
        )
    return graph.compile()


async def _noop(state: PipelineState) -> dict[str, Any]:
    return {}


def _router(nxt: str | None) -> Callable[[PipelineState], str]:
    def route(state: PipelineState) -> str:
        if state.get("stopped_at") or nxt is None:
            return "end"
        return "next"

    return route


async def run_steps(
    lead_id: uuid.UUID,
    steps: Sequence[Step],
    adapters: Adapters | None = None,
    *,
    timeouts: Mapping[str, float] | None = None,
    force_hooks: Mapping[str, ForceHook] | None = None,
) -> PipelineState:
    graph = build_graph(
        load_agents(), adapters or get_adapters(), steps=steps,
        timeouts=timeouts, force_hooks=force_hooks,
    )  # fmt: skip
    result: PipelineState = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    logger.info(
        "lead %s: completed=%s stopped_at=%s",
        lead_id,
        result.get("completed"),
        result.get("stopped_at"),
    )
    return result


async def run_lead(lead_id: uuid.UUID, adapters: Adapters | None = None) -> PipelineState:
    """Run or resume one lead in its lane(s)."""
    from app.orchestrator import lanes  # noqa: PLC0415 - lanes builds on this module

    return await lanes.continue_lead(lead_id, adapters)
