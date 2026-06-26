"""
Regression test for HT-E398C6: /new --scenario forces the pipeline scenario.

The Slack handler parsed --scenario but never forwarded it, so the orchestrator
re-classified the prompt (e.g. an Android prompt ran as poc instead of
production). The scenario now flows: /new -> run_pipeline(scenario=) ->
new_state(scenario_override) -> orchestrator honors it.
"""

import pytest

from core.state.pipeline_state import new_state
import agents.orchestrator.agent as orch


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    import core.notifications.slack as slack
    for fn in ("post", "alert", "status", "approval_request", "deployment"):
        monkeypatch.setattr(slack, fn, lambda *a, **k: True)
    monkeypatch.setattr(orch.registry_store, "create_ticket", lambda *a, **k: None)
    # If the override works, classify() must NOT be called — make it explode if it is.
    monkeypatch.setattr(orch, "classify", lambda *a, **k: (_ for _ in ()).throw(AssertionError("classify should be skipped")))
    yield


def test_new_state_records_override():
    s = new_state("HT-OV1", "Build an Android TPV app", scenario="production")
    assert s["scenario_override"] == "production"
    assert s["scenario"] == "production"


def test_orchestrator_honors_override_for_production():
    s = new_state("HT-OV2", "Build an Android TPV app with a payment button", scenario="production")
    out = orch.run(s)
    assert out["scenario"] == "production"
    # production plan includes the review/security/devops agents
    plan = out["agent_plan"]
    assert plan[0] == "orchestrator"
    assert {"ba_compliance", "architect", "security", "devops"}.issubset(set(plan))


def test_orchestrator_honors_override_for_internal():
    s = new_state("HT-OV3", "Build an Android TPV app", scenario="internal")
    out = orch.run(s)
    assert out["scenario"] == "internal"
