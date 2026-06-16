"""
LangGraph workflow — defines the agent graph and conditional routing.

POC path:
  orchestrator → coder → infra → END

Internal path:
  orchestrator → ba_compliance → ux_ui → END

Production path:
  orchestrator → ba_compliance → ux_ui → architect → pr_review → security → END
"""

from langgraph.graph import StateGraph, END
from core.state.pipeline_state import PipelineState
from agents.orchestrator import agent as orchestrator
from agents.coder import agent as coder
from agents.infra import agent as infra
from agents.ba_compliance import agent as ba_compliance
from agents.ux_ui import agent as ux_ui
from agents.architect import agent as architect
from agents.pr_review import agent as pr_review
from agents.security import agent as security


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


def _route_from_ux_ui(state: PipelineState) -> str:
    """Production scenario continues to architect; internal stops here."""
    scenario = state.get("scenario", "internal")
    if scenario == "production":
        return "architect"
    return END


def _route_from_pr_review(state: PipelineState) -> str:
    """Blocked review skips security; approved/changes_requested continue."""
    next_agent = state.get("next_agent")
    if next_agent == "security":
        return "security"
    return END


def build() -> StateGraph:
    graph = StateGraph(PipelineState)

    graph.add_node("orchestrator", orchestrator.run)
    graph.add_node("coder", coder.run)
    graph.add_node("infra", infra.run)
    graph.add_node("ba_compliance", ba_compliance.run)
    graph.add_node("ux_ui", ux_ui.run)
    graph.add_node("architect", architect.run)
    graph.add_node("pr_review", pr_review.run)
    graph.add_node("security", security.run)

    graph.set_entry_point("orchestrator")

    graph.add_conditional_edges("orchestrator", _route_from_orchestrator, {
        "coder": "coder",
        "ba_compliance": "ba_compliance",
        END: END,
    })

    # POC path: coder → infra → done
    graph.add_edge("coder", "infra")
    graph.add_edge("infra", END)

    # BA → UX/UI (both internal and production)
    graph.add_conditional_edges("ba_compliance", _route_from_ba, {
        "ux_ui": "ux_ui",
        END: END,
    })

    # UX/UI → architect (production only) or END (internal)
    graph.add_conditional_edges("ux_ui", _route_from_ux_ui, {
        "architect": "architect",
        END: END,
    })

    # Production path: architect → pr_review → security → done
    graph.add_edge("architect", "pr_review")
    graph.add_conditional_edges("pr_review", _route_from_pr_review, {
        "security": "security",
        END: END,
    })
    graph.add_edge("security", END)

    return graph.compile()
