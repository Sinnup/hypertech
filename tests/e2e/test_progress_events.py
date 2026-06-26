"""
Tests for HT-B14F43: plan/progress event framework + report_step.

Verifies emit_plan / emit_progress publish the right event shapes on the bus,
and that report_step posts to Slack AND emits a viz substep.
"""

import core.events.graph_events as ge
from core.events.event_bus import event_bus
from core.events.reporting import report_step


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_emit_plan_and_progress(monkeypatch):
    monkeypatch.setattr(ge, "_post_event", lambda *a, **k: None)  # no network
    q = event_bus.subscribe("HT-EV1")
    ge.emit_plan("HT-EV1", "pipeline", "production",
                 [{"id": "orchestrator", "label": "Orchestrator"}])
    ge.emit_progress("HT-EV1", "ba_compliance", "Querying KB…", "detail")
    evs = _drain(q)
    event_bus.unsubscribe("HT-EV1", q)

    types = [e["type"] for e in evs]
    assert "plan_ready" in types and "agent_progress" in types
    plan = next(e for e in evs if e["type"] == "plan_ready")
    assert plan["data"]["kind"] == "pipeline"
    assert plan["data"]["steps"][0]["id"] == "orchestrator"
    prog = next(e for e in evs if e["type"] == "agent_progress")
    assert prog["data"]["agent"] == "ba_compliance"
    assert prog["data"]["detail"] == "detail"


def test_report_step_posts_slack_and_emits(monkeypatch):
    monkeypatch.setattr(ge, "_post_event", lambda *a, **k: None)
    import core.notifications.slack as slack
    posted = []
    monkeypatch.setattr(slack, "status", lambda tid, msg: posted.append((tid, msg)) or True)

    q = event_bus.subscribe("HT-EV2")
    report_step("HT-EV2", "coder", "Generating code…")
    evs = _drain(q)
    event_bus.unsubscribe("HT-EV2", q)

    assert posted and posted[0][1] == "Generating code…"
    assert any(e["type"] == "agent_progress" and e["data"]["agent"] == "coder" for e in evs)
