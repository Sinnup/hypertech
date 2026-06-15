"""
LangGraph workflow — defines the agent graph and conditional routing.
Monday scope: orchestrator → coder → END (POC path).
"""

from langgraph.graph import StateGraph, END
from core.state.pipeline_state import PipelineState
from agents.orchestrator import agent as orchestrator
from agents.coder import agent as coder


def _route(state: PipelineState) -> str:
    """Conditional edge: routes to next agent based on state."""
    next_agent = state.get("next_agent")
    if next_agent == "coder":
        return "coder"
    # internal / production → ba_compliance (Tuesday+)
    return END


def build() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("orchestrator", orchestrator.run)
    graph.add_node("coder", coder.run)

    graph.set_entry_point("orchestrator")
    graph.add_conditional_edges("orchestrator", _route, {
        "coder": "coder",
        END: END,
    })
    graph.add_edge("coder", END)

    return graph.compile()
