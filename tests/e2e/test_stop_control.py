"""
Tests for HT-337916: Stop control.

  * cancel.request_stop / is_stop_requested / clear_stop round-trip.
  * stream_pipeline halts at the next node boundary when a stop is requested,
    keeps the checkpoint (resumable), and emits pipeline_stopped.
"""

import pytest

import core.control.cancel as cancel
import core.events.graph_events as ge
import core.checkpoint.manager as cp
from core.state.pipeline_state import new_state


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cancel, "_CANCEL_DIR", tmp_path / "cancel")
    monkeypatch.setattr(cp, "_CHECKPOINT_DIR", tmp_path / "ckpt")
    import core.state.state_manager as sm
    monkeypatch.setattr(sm, "update_pipeline_status", lambda *a, **k: None)
    yield


def test_cancel_roundtrip():
    cancel.request_stop("HT-STOP1")
    assert cancel.is_stop_requested("HT-STOP1") is True
    cancel.clear_stop("HT-STOP1")
    assert cancel.is_stop_requested("HT-STOP1") is False


class _FakeGraph:
    def __init__(self, nodes):
        self._nodes = nodes

    def stream(self, state, stream_mode=None, config=None):
        for n in self._nodes:
            yield {n: {"current_agent": n, "status": f"ran_{n}"}}


def test_stream_halts_on_stop(monkeypatch):
    events = []
    monkeypatch.setattr(ge, "emit_event", lambda tid, t, d=None: events.append((t, d)))

    ticket = "HT-STOP2"
    cancel.request_stop(ticket)  # stop set before run → halts after the first node

    s = new_state(ticket, "p")
    s["agent_plan"] = ["orchestrator", "coder", "infra"]
    ge.stream_pipeline(_FakeGraph(["orchestrator", "coder", "infra"]), s, ticket)

    types = [t for t, _ in events]
    assert "pipeline_stopped" in types
    # halted early — did not process all three nodes
    starts = [d.get("agent") for t, d in events if t == "agent_start"]
    assert starts == ["orchestrator"], f"should stop after first node, got {starts}"
    # checkpoint kept for resume; stop flag cleared
    assert cp.exists(ticket)
    assert cancel.is_stop_requested(ticket) is False
