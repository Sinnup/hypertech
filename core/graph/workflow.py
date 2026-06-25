"""
LangGraph workflow — dynamic agent graph with validation gate and context packer.

Phase 1 topology (backward compatible)::

    entry → orchestrator
    orchestrator → validation_gate
    validation_gate → context_packer   (ok)
    validation_gate → human_escalation (low confidence / failed)
    context_packer → _plan_router → [next agent] → validation_gate → ...
    human_escalation → END
    _plan_router → END  (when no more agents)

Legacy fallback — when ``agent_plan`` is empty (existing orchestrator doesn't set it),
``_plan_router`` reads ``next_agent`` from state and routes identically to the old
hardcoded paths.  Every agent writes ``current_agent`` and ``next_agent`` as before
so the old POC / Internal / Production flows work unchanged.
"""

from langgraph.graph import StateGraph, END
from core.state.pipeline_state import PipelineState
from core.agent_registry import discover_agents, get_agents
from core.agent_registry.validation_gate import validation_gate_node, route_from_gate
from agents.context_packer.agent import run as context_packer_run


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build() -> StateGraph:
    """Build and compile the dynamic agent graph."""
    discover_agents()
    agents = get_agents()

    graph = StateGraph(PipelineState)

    # -- Register all discovered agent nodes ---------------------------------
    for name, defn in agents.items():
        if defn.visible and defn.fn is not None:
            graph.add_node(name, defn.fn)

    # -- Infrastructure nodes (always present) --------------------------------
    # Note: context_packer is auto-discovered via @register — skip if already present.
    if "context_packer" not in agents:
        graph.add_node("context_packer", context_packer_run)
    graph.add_node("validation_gate", validation_gate_node)
    graph.add_node("human_escalation", _human_escalation)

    # -- Entry point ----------------------------------------------------------
    graph.set_entry_point("orchestrator")

    # -- Orchestrator → validation_gate ---------------------------------------
    graph.add_edge("orchestrator", "validation_gate")

    # -- Validation gate → context_packer or human_escalation -----------------
    graph.add_conditional_edges(
        "validation_gate",
        route_from_gate,
        {
            "context_packer": "context_packer",
            "human_escalation": "human_escalation",
        },
    )

    # -- Context packer → next agent in plan (or END) -------------------------
    graph.add_conditional_edges(
        "context_packer",
        _plan_router,
        {},  # dynamic destinations — _plan_router returns agent name or END
    )

    # -- Every visible agent (except orchestrator) → validation_gate ----------
    for name in agents:
        if name != "orchestrator" and agents[name].visible:
            graph.add_edge(name, "validation_gate")

    # -- Human escalation is terminal ----------------------------------------
    graph.add_edge("human_escalation", END)

    return graph.compile()


def make_graph():
    """Factory for LangGraph Studio / ``langgraph dev`` (see ``langgraph.json``).

    Returns the compiled StateGraph so the standard LangGraph tooling can render
    the live node/edge graph and stream executions — no custom wrappers needed.
    """
    return build()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _plan_router(state: PipelineState) -> str:
    """Return the next agent to execute, or END.

    Priority:
    1. ``agent_plan`` — set by enhanced orchestrator (new path).
    2. ``next_agent`` — set by legacy agents (old path, backward compat).
    3. END — no more work.
    """
    # Priority 1: dynamic agent_plan
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    if plan and idx < len(plan):
        next_up = plan[idx]
        # Safety: don't route to self or to non-existent agents
        if next_up != state.get("current_agent", ""):
            return next_up
        # Skip self, advance to next
        idx += 1
        state["agent_plan_index"] = idx
        if idx < len(plan):
            return plan[idx]
        return END

    # Priority 2: legacy next_agent
    nxt = state.get("next_agent")
    if nxt and nxt != state.get("current_agent", ""):
        return nxt

    return END


# ---------------------------------------------------------------------------
# Human escalation — terminal node
# ---------------------------------------------------------------------------

def _human_escalation(state: PipelineState) -> PipelineState:
    """Terminal node: pipeline stops and waits for human review."""
    from datetime import datetime, timezone

    state["status"] = "human_escalation"
    state["current_agent"] = "human_escalation"
    state["next_agent"] = None
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    return state
