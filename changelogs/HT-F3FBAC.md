# HT-F3FBAC — Circuit breaker no longer hangs on a wedged dependency

- **Scenario**: fix
- **Agents**: ba_compliance (and any circuit-breaker-wrapped call)
- **Branch**: feature/HT-F3FBAC
- **Created**: 2026-06-26 03:15 UTC
- **Status**: completed

## Summary
A production run hung at `ba_compliance` ("extracting requirements…"). Stack
dump showed the KB query stuck constructing the ChromaDB `HttpClient`
(`get_database` HTTP call never returned). The circuit breaker should have
timed out after 10 s and fallen back to an empty KB — but it used
`with ThreadPoolExecutor() as pool:`, whose `__exit__` calls
`shutdown(wait=True)`. So even after `future.result(timeout=...)` raised
TimeoutError, exiting the `with` block blocked forever on the hung worker, and
the `except TimeoutError` never ran. The breaker could never time out.

## Changes
- **core/circuit_breaker/decorator.py**: manage the executor manually; on
  timeout/exception, `pool.shutdown(wait=False, cancel_futures=True)` so a hung
  worker never blocks the pipeline. The breaker now returns the fallback within
  `timeout` regardless of a wedged primary.
- **tests/e2e/test_circuit_breaker_timeout.py** (new): a 60 s-hung primary falls
  back within the timeout; a fast primary returns normally.

## Verification
`uv run pytest tests/e2e/test_circuit_breaker_timeout.py` → 2 passed
(hung primary returns FALLBACK in ~2 s, not 60 s).

## Commits
- (pending)
