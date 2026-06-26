"""
Regression test for HT-F3FBAC: the circuit breaker must time out a hung call.

It used `with ThreadPoolExecutor() as pool:`, whose __exit__ does
shutdown(wait=True) — so even after future.result() timed out, exiting the block
blocked forever on the hung worker. A wedged ChromaDB HttpClient therefore hung
ba_compliance indefinitely. The breaker must now return the fallback within the
timeout regardless of a hung primary.
"""

import time

import pytest

from core.circuit_breaker.decorator import circuit_breaker


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    import core.notifications.slack as slack
    for fn in ("post", "alert", "status", "approval_request", "deployment"):
        monkeypatch.setattr(slack, fn, lambda *a, **k: True)
    yield


def test_hung_primary_falls_back_within_timeout():
    @circuit_breaker(service_name="hang", fallback=lambda: "FALLBACK",
                     fallback_label="empty", timeout=2.0, require_approval=False)
    def hung():
        time.sleep(60)  # simulate a wedged dependency
        return "PRIMARY"

    t = time.time()
    result = hung()
    elapsed = time.time() - t
    assert result == "FALLBACK"
    assert elapsed < 8, f"breaker blocked on hung worker for {elapsed:.1f}s"


def test_fast_primary_returns_normally():
    @circuit_breaker(service_name="fast", fallback=lambda: "FB",
                     fallback_label="empty", timeout=5.0, require_approval=False)
    def fast():
        return "PRIMARY"

    assert fast() == "PRIMARY"
