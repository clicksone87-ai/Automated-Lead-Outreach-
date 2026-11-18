"""Per-lead LangGraph workflow.

Completed steps are skipped on re-entry, so re-invoking the graph resumes at the first
unfinished step. `lane` picks the steps (see `lanes`): the worker runs the fast lane for every
lead and the heavy lane on its own queue; the CLI runs everything in one go.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.adapters.registry import Adapters, get_adapters
from app.config import Settings, get_settings
from app.db.session import session_scope
from app.orchestrator import lanes, runs
from app.orchestrator.agents import PIPELINE_ORDER, AgentContext, LeadAgent, load_agents

LANES: dict[str, tuple[str, ...]] = {
    "full": lanes.FULL,
    "fast": lanes.FAST_LANE,
    "heavy": lanes.HEAVY_LANE,
}

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    lead_id: str
    completed: list[str]
    stopped_at: str | None
    reason: str


NodeFn = Callable[[PipelineState], Awaitable[dict[str, Any]]]


def _node(
    agent: LeadAgent, adapters: Adapters, settings: Settings, *, redraft: bool = False
) -> NodeFn:
    async def run_node(state: PipelineState) -> dict[str, Any]:
        lead_id = uuid.UUID(state["lead_id"])
        async with session_scope() as session:
            ctx = AgentContext(session=session, adapters=adapters, settings=settings)
            # Heavy lane: replace a still-pending fast-lane draft with one that has assets.
            force = redraft and await lanes.supersede_pending_proposal(session, lead_id)
            outcome = await runs.execute(ctx, agent, lead_id, force=force)
        completed = [*state.get("completed", []), agent.name]
        if outcome.proceed:
            return {"completed": completed}
        return {"completed": completed, "stopped_at": agent.name, "reason": outcome.reason}

    return run_node


def build_graph(
    agents: dict[str, LeadAgent],
    adapters: Adapters,
    settings: Settings | None = None,
    *,
    lane: str = "full",
) -> Any:
    settings = settings or get_settings()
    wanted = LANES[lane]
    steps = [name for name in PIPELINE_ORDER if name in agents and name in wanted]
    graph: Any = StateGraph(PipelineState)
    if not steps:
        graph.add_node("noop", _noop)
        graph.add_edge(START, "noop")
        graph.add_edge("noop", END)
        return graph.compile()
    for name in steps:
        redraft = lane == "heavy" and name == "proposal"
        graph.add_node(name, _node(agents[name], adapters, settings, redraft=redraft))
    graph.add_edge(START, steps[0])
    for current, nxt in zip(steps, [*steps[1:], None], strict=True):
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


async def run_lead(
    lead_id: uuid.UUID, adapters: Adapters | None = None, *, lane: str = "full"
) -> PipelineState:
    graph = build_graph(load_agents(), adapters or get_adapters(), lane=lane)
    result: PipelineState = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    logger.info(
        "%s lane for %s: completed=%s stopped_at=%s",
        lane,
        lead_id,
        result.get("completed"),
        result.get("stopped_at"),
    )
    return result
