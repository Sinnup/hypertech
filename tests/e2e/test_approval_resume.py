"""
Tests for HT-APPROVE: escalation checkpoints survive for auto-resume.

Drives ``stream_pipeline`` with a fake graph so we can assert the checkpoint
lifecycle without any LLM calls:
  * an escalated run KEEPS its checkpoint (so an approval can resume it)
  * a clean run DELETES its checkpoint on completion (existing behaviour)
"""

from pathlib import Path

import pytest

import core.events.graph_events as ge
import core.checkpoint.manager as cp
from core.state.pipeline_state import new_state


class _FakeGraph:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, state, stream_mode=None, config=None):
        for c in self._chunks:
            yield c


@pytest.fixture(autouse=True)
def _tmp_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    # Silence the cross-process viz POST + dispatcher writes during the test.
    monkeypatch.setattr(ge, "emit_event", lambda *a, **k: None)
    import core.state.state_manager as sm
    monkeypatch.setattr(sm, "update_pipeline_status", lambda *a, **k: None)
    yield


def _state(ticket):
    s = new_state(ticket, "build a compliance tool")
    s["agent_plan"] = ["orchestrator", "ba_compliance", "infra"]
    s["agent_plan_index"] = 1
    s["current_agent"] = "ba_compliance"
    return s


def test_escalation_checkpoint_is_kept():
    ticket = "HT-ESC001"
    chunks = [{
        "validation_gate": {
            "human_escalation": True,
            "current_agent": "ba_compliance",
            "status": "human_escalation",
        }
    }]
    ge.stream_pipeline(_FakeGraph(chunks), _state(ticket), ticket)
    assert cp.exists(ticket), "escalation checkpoint must survive for resume"


def test_clean_completion_deletes_checkpoint():
    ticket = "HT-OK001"
    chunks = [{
        "validation_gate": {
            "human_escalation": False,
            "current_agent": "ba_compliance",
            "status": "compliance_checked",
        }
    }]
    ge.stream_pipeline(_FakeGraph(chunks), _state(ticket), ticket)
    assert not cp.exists(ticket), "completed run should clean up its checkpoint"
