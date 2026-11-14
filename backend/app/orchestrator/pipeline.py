"""Per-lead LangGraph workflow.

Each node runs one agent in its own transaction. Completed steps are skipped on re-entry
(agent_runs), so re-invoking the graph resumes from the first unfinished step without
restarting the cycle. A step that says `proceed=False` ends the graph for that lead.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.adapters.registry import Adapters, get_adapters
from app.config import get_settings
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import PIPELINE_ORDER, AgentContext, LeadAgent, load_agents

logger = logging.getLogger(__name__)


class PipelineState(TypedDict, total=False):
    lead_id: str
    completed: list[str]
    stopped_at: str | None
    reason: str


NodeFn = Callable[[PipelineState], Awaitable[dict[str, Any]]]


def _node(agent: LeadAgent, adapters: Adapters) -> NodeFn:
    async def run_node(state: PipelineState) -> dict[str, Any]:
        lead_id = uuid.UUID(state["lead_id"])
        async with session_scope() as session:
            ctx = AgentContext(session=session, adapters=adapters, settings=get_settings())
            outcome = await runs.execute(ctx, agent, lead_id)
        completed = [*state.get("completed", []), agent.name]
        if outcome.proceed:
            return {"completed": completed}
        return {"completed": completed, "stopped_at": agent.name, "reason": outcome.reason}

    return run_node


def build_graph(agents: dict[str, LeadAgent], adapters: Adapters) -> Any:
    steps = [name for name in PIPELINE_ORDER if name in agents]
    graph: Any = StateGraph(PipelineState)
    if not steps:
        graph.add_node("noop", _noop)
        graph.add_edge(START, "noop")
        graph.add_edge("noop", END)
        return graph.compile()
    for name in steps:
        graph.add_node(name, _node(agents[name], adapters))
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


async def run_lead(lead_id: uuid.UUID, adapters: Adapters | None = None) -> PipelineState:
    graph = build_graph(load_agents(), adapters or get_adapters())
    result: PipelineState = await graph.ainvoke(
        {"lead_id": str(lead_id), "completed": [], "stopped_at": None, "reason": ""}
    )
    logger.info(
        "pipeline for %s: completed=%s stopped_at=%s",
        lead_id,
        result.get("completed"),
        result.get("stopped_at"),
    )
    return result
