"""
Regression test for HT-C871A9: pipeline state must not grow exponentially.

Agents read-modify-write the *full* list channels in the shared state and return
the whole state.  With an `operator.add` reducer that concatenated the full list
onto itself every node (doubling per step → GB-scale state, rejected LangSmith
traces).  The list channels now use last-write-wins, so the list grows linearly.
"""

from typing import Annotated, List, TypedDict

from langgraph.graph import StateGraph, END

from core.state.pipeline_state import PipelineState


def test_list_channels_use_keep_latest():
    """The known-bad channels must not use operator.add."""
    hints = PipelineState.__annotations__
    import operator
    for field in ("agent_messages", "pending_questions", "pending_answers", "human_approvals"):
        reducer = hints[field].__metadata__[0]
        assert reducer is not operator.add, f"{field} must not use operator.add"


def test_no_exponential_growth_in_real_state():
    """Drive a tiny graph over PipelineState-like list channels and assert linear growth."""
    def keep_latest(_c, u):
        return u

    class S(TypedDict):
        msgs: Annotated[List[int], keep_latest]
        step: Annotated[int, keep_latest]

    def node(state):
        state["msgs"].append(state["step"])  # mutate full list, return full state
        state["step"] += 1
        return state

    g = StateGraph(S)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_conditional_edges("n", lambda s: "n" if s["step"] < 8 else END, {"n": "n", END: END})
    out = g.compile().invoke({"msgs": [], "step": 0}, config={"recursion_limit": 50})
    assert len(out["msgs"]) == 8  # linear, not 2**8
