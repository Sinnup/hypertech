"""
Langfuse tracing — DISABLED / backlogged (HT-20911B).

Langfuse (OTEL-based ``@observe`` + cost tracking) was removed from the runtime
in favour of the standard LangChain / LangGraph / LangSmith ecosystem, so the
live node/edge graph (LangGraph Studio) and nested run traces (LangSmith) are
visible and replicable by anyone at the company.  Keeping Langfuse's OTEL spans
active also caused pipeline hangs when its ingest endpoint (localhost:3000) was
slow, and double-instrumented the LangSmith traces.

This module is now a no-op shim that preserves the old public API so the rest of
the codebase keeps importing it unchanged:

  * ``observe``              — passthrough decorator (no tracing)
  * ``pipeline_trace``       — context manager yielding None
  * ``record_generation`` / ``record_pipeline_output`` / ``get_client`` /
    ``flush`` — no-ops
  * ``get_token_usage`` / ``sum_token_usage`` — kept (pure, provider-agnostic)

Backlog: to restore Langfuse cost tracking, re-implement these against the
Langfuse client (the original is in git history) and switch agent imports back
to ``from langfuse import observe``.
"""

from contextlib import contextmanager


def observe(*dargs, **dkwargs):
    """No-op replacement for langfuse's ``@observe``.

    Supports both bare ``@observe`` and parameterised ``@observe(name="...")``.
    """
    if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
        return dargs[0]  # used as @observe

    def _decorator(fn):
        return fn
    return _decorator


def get_client():
    """Langfuse disabled — always None (callers are null-safe)."""
    return None


@contextmanager
def pipeline_trace(ticket_id: str, prompt: str, scenario: str = None):
    """No-op root span — yields None so callers stay branch-free."""
    yield None


def record_pipeline_output(output: dict):
    return None


def record_generation(model: str, result, output: str = None):
    return None


def get_token_usage(result) -> dict[str, int]:
    """Extract input/output token counts from an LLM result (provider-agnostic).

    Kept because it is pure and useful independent of Langfuse.
    """
    if hasattr(result, "usage_metadata") and result.usage_metadata:
        return {
            "input": result.usage_metadata.get("input_tokens", 0),
            "output": result.usage_metadata.get("output_tokens", 0),
        }
    usage = (getattr(result, "response_metadata", None) or {}).get("token_usage", {})
    if usage:
        return {
            "input": (usage.get("input_tokens") or usage.get("prompt_tokens")
                      or usage.get("inputTokenCount") or 0),
            "output": (usage.get("output_tokens") or usage.get("completion_tokens")
                       or usage.get("outputTokenCount") or 0),
        }
    return {"input": 0, "output": 0}


def sum_token_usage(*results) -> dict[str, int]:
    total_in, total_out = 0, 0
    for r in results:
        u = get_token_usage(r)
        total_in += u["input"]
        total_out += u["output"]
    return {"input": total_in, "output": total_out}


def flush(timeout: float = 10.0):
    return None
