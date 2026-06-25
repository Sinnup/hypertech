"""
Unit tests for the HitL autopilot driving the validation gate.

Fast (no LLM, no Slack): we construct a low-confidence agent output and assert
that HITL_AUTOPILOT deterministically resolves the escalation the same way a
human clicking the Slack button would.
"""

import pytest

from core.agent_registry import hitl_autopilot
from core.agent_registry.validation_gate import validation_gate_node
from core.state.pipeline_state import new_state


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    """Silence Slack so tests never post to a real channel."""
    import core.notifications.slack as slack
    for fn in ("post", "alert", "status", "approval_request", "deployment"):
        monkeypatch.setattr(slack, fn, lambda *a, **k: True)
    hitl_autopilot.reset()
    yield
    hitl_autopilot.reset()


def _low_conf_state(agent="ba_compliance"):
    state = new_state("HT-AUTOPILOT", "test prompt")
    state["current_agent"] = agent
    # A degraded, low-confidence output that would normally escalate to a human.
    state["agent_outputs"][agent] = {
        "status": "degraded",
        "data": {},
        "confidence": 0.30,
        "agent_name": agent,
        "validation_errors": ["needs human review"],
    }
    return state


def test_idle_escalates(monkeypatch):
    monkeypatch.delenv("HITL_AUTOPILOT", raising=False)
    out = validation_gate_node(_low_conf_state())
    assert out["human_escalation"] is True


def test_approve_continues(monkeypatch):
    monkeypatch.setenv("HITL_AUTOPILOT", "approve")
    out = validation_gate_node(_low_conf_state())
    assert out["human_escalation"] is False
    assert out["agent_outputs"]["ba_compliance"]["status"] == "ok"


def test_reject_fails(monkeypatch):
    monkeypatch.setenv("HITL_AUTOPILOT", "reject")
    out = validation_gate_node(_low_conf_state())
    assert out["human_escalation"] is True
    assert out["status"] == "failed"


def test_changes_first_then_approve(monkeypatch):
    monkeypatch.setenv("HITL_AUTOPILOT", "changes")
    # First evaluation → changes requested (escalates with feedback)
    out1 = validation_gate_node(_low_conf_state())
    assert out1["human_escalation"] is True
    assert "changes requested" in (out1["human_escalation_reason"] or "").lower()
    # Second evaluation of the same stage → approved (modify-then-accept)
    out2 = validation_gate_node(_low_conf_state())
    assert out2["human_escalation"] is False
