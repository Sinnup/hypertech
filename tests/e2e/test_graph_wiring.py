"""
Regression test for HT-C871A9: context_packer must not fan out to the gate.

context_packer is registered via @register (so it appears in the agent list).
The "every agent -> validation_gate" loop must exclude it, otherwise it gets a
static edge to validation_gate on top of its conditional _plan_router edge — a
fan-out that loops the gate forever (GraphRecursionError).
"""

from collections import defaultdict

from core.graph.workflow import build


def _edges():
    g = build().get_graph()
    out = defaultdict(list)
    for e in g.edges:
        out[e.source].append(e.target)
    return out


def test_context_packer_does_not_route_to_gate():
    out = _edges()
    assert "validation_gate" not in out.get("context_packer", []), (
        "context_packer must route only via _plan_router, not to validation_gate"
    )


def test_orchestrator_routes_to_gate_once():
    out = _edges()
    assert out.get("orchestrator") == ["validation_gate"]
