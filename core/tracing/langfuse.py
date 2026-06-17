"""
Langfuse tracing — Langfuse v4 (OTEL-based).

Why this shape:
  LangGraph runs every agent node synchronously inside a single graph.invoke()
  call, and Langfuse's @observe spans nest by OpenTelemetry context on that
  thread. So we open ONE root span around the whole pipeline run
  (``pipeline_trace``) and every agent's @observe span — and every LLM
  generation recorded with ``update_current_generation`` — lands inside that
  one trace. Token usage and cost then roll up to the pipeline level instead of
  being scattered across one disconnected trace per node.

API notes (v4.8):
  - ``langfuse.decorators`` / ``langfuse_context`` do NOT exist in v4. Use the
    client's ``update_current_generation`` / ``update_current_span`` instead.
  - Token usage goes in ``usage_details=`` (not ``usage=``).
  - Trace I/O is set on the root observation directly — ``input=`` arg on
    ``start_as_current_observation`` / ``update(output=...)`` on the yielded span.
    ``set_current_trace_io`` is deprecated in v4.8+.
  - For trace attributes (user_id, session_id, tags), use
    ``client.propagate_attributes()`` context manager.
"""

import os
from contextlib import contextmanager

from langfuse import Langfuse
from core.secrets.loader import get_optional

_HOST = get_optional("LANGFUSE_HOST", "http://localhost:3000")
_PUBLIC_KEY = get_optional("LANGFUSE_PUBLIC_KEY")
_SECRET_KEY = get_optional("LANGFUSE_SECRET_KEY")

if _PUBLIC_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = _PUBLIC_KEY
if _SECRET_KEY:
    os.environ["LANGFUSE_SECRET_KEY"] = _SECRET_KEY
if _HOST:
    os.environ["LANGFUSE_HOST"] = _HOST

_client: Langfuse = None


def get_client() -> Langfuse:
    """Return the configured Langfuse client, or None if keys are absent."""
    global _client
    if _client is None and _PUBLIC_KEY and _SECRET_KEY:
        _client = Langfuse(
            public_key=_PUBLIC_KEY,
            secret_key=_SECRET_KEY,
            host=_HOST,
        )
    return _client


@contextmanager
def pipeline_trace(ticket_id: str, prompt: str, scenario: str = None):
    """
    Root span for one pipeline run. Wrap ``graph.invoke()`` in this so every
    agent span and LLM generation nests into a single trace.

    The trace id is seeded from the ticket id so re-runs of the same ticket map
    to a stable, predictable trace. Yields the root span (or None if Langfuse
    is not configured, so callers stay null-safe without branching).
    """
    client = get_client()
    if not client:
        yield None
        return

    trace_id = client.create_trace_id(seed=ticket_id)
    # Setting input= on the root observation automatically surfaces it
    # as trace-level input (v4 default behaviour).
    with client.start_as_current_observation(
        name="pipeline-run",
        trace_context={"trace_id": trace_id},
        input={"prompt": prompt},
        metadata={"ticket_id": ticket_id, "scenario": scenario},
    ) as span:
        yield span


def record_pipeline_output(output: dict):
    """
    Record the final pipeline output on the root span.
    Must be called while still inside ``pipeline_trace`` (i.e. before the
    context manager exits) so the current observation is the root span.

    The root observation's output automatically becomes trace-level output
    in Langfuse v4 — no separate ``set_current_trace_io`` call needed.
    """
    client = get_client()
    if not client:
        return
    client.update_current_span(output=output)


def flush(timeout: float = 10.0):
    """Flush pending spans with a deadline so the pipeline never hangs."""
    client = get_client()
    if not client:
        return
    import threading
    done = threading.Event()

    def _flush():
        try:
            client.flush()
        finally:
            done.set()

    t = threading.Thread(target=_flush, daemon=True)
    t.start()
    done.wait(timeout=timeout)
