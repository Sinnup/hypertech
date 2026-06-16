"""
LangGraph workflow — defines the agent graph and conditional routing.

POC path:    orchestrator → coder → infra → END
Internal:    orchestrator → ba_compliance → ux_ui → END
Production:  orchestrator → ba_compliance → ux_ui → END (full Thursday loop TBD)
"""

from langgraph.graph import StateGraph, END
from core.state.pipeline_state import PipelineState
from agents.orchestrator import agent as orchestrator
from agents.coder import agent as coder
from agents.infra import agent as infra
from agents.ba_compliance import agent as ba_compliance
from agents.ux_ui import agent as ux_ui


def _route_from_orchestrator(state: PipelineState) -> str:
    next_agent = state.get("next_agent")
    if next_agent == "coder":
        return "coder"
    if next_agent == "ba_compliance":
        return "ba_compliance"
    return END


def _route_from_ba(state: PipelineState) -> str:
    next_agent = state.get("next_agent")
    if next_agent == "ux_ui":
        return "ux_ui"
    return END


def build() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("orchestrator", orchestrator.run)
    graph.add_node("coder", coder.run)
    graph.add_node("infra", infra.run)
    graph.add_node("ba_compliance", ba_compliance.run)
    graph.add_node("ux_ui", ux_ui.run)

    graph.set_entry_point("orchestrator")

    graph.add_conditional_edges("orchestrator", _route_from_orchestrator, {
        "coder": "coder",
        "ba_compliance": "ba_compliance",
        END: END,
    })

    # POC path: coder → infra → done
    graph.add_edge("coder", "infra")
    graph.add_edge("infra", END)

    # Internal / production path: ba_compliance → ux_ui → done
    graph.add_conditional_edges("ba_compliance", _route_from_ba, {
        "ux_ui": "ux_ui",
        END: END,
    })
    graph.add_edge("ux_ui", END)

    return graph.compile()
