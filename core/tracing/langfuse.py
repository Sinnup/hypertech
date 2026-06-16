"""
Langfuse tracing — @observe decorators + manual spans (Langfuse v4, OTEL-based).
"""

import os
from langfuse import Langfuse, observe
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
    global _client
    if _client is None and _PUBLIC_KEY and _SECRET_KEY:
        _client = Langfuse(
            public_key=_PUBLIC_KEY,
            secret_key=_SECRET_KEY,
            host=_HOST,
        )
    return _client


def get_callback(trace_id: str = None, session_id: str = None):
    """No-op — LangChain callbacks replaced by @observe in v4."""
    return None


def _trace_id_for(ticket_id: str) -> str:
    client = get_client()
    if not client:
        return None
    return client.create_trace_id(seed=ticket_id)


def trace_pipeline(ticket_id: str, prompt: str, scenario: str = None):
    """Open a root trace span for the pipeline run."""
    client = get_client()
    if not client:
        return None
    trace_id = _trace_id_for(ticket_id)
    span = client.start_observation(
        name="pipeline-run",
        trace_context={"trace_id": trace_id},
        input={"prompt": prompt},
        metadata={"scenario": scenario, "ticket_id": ticket_id},
    )
    span.end()
    return span


def update_pipeline_output(ticket_id: str, output: dict):
    """Record final pipeline output on the root trace."""
    client = get_client()
    if not client:
        return
    trace_id = _trace_id_for(ticket_id)
    span = client.start_observation(
        name="pipeline-complete",
        trace_context={"trace_id": trace_id},
        output=output,
    )
    span.end()


def flush():
    client = get_client()
    if client:
        client.flush()
