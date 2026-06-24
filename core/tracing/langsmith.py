"""
LangSmith tracing — LangGraph-native callback for node/edge topology visibility.

Why alongside Langfuse:
  Langfuse sees the pipeline as a flat OTEL span tree — it has no concept of
  LangGraph nodes, edges, state transitions, or DAG structure.  LangSmith's
  ``LangChainTracer`` is a native LangChain callback that hooks into LangGraph's
  callback system and captures the full graph execution model:

  - Graph DAG structure and topology
  - Node execution order with timing
  - Edge transitions (which node → which node)
  - State diffs between nodes
  - LLM calls grouped per-node (prompt + response)
  - Token usage with cost per node
  - Conditional edge routing
  - Recursion/loop visualization

Coexistence:
  LangSmith uses LangChain's callback system.  Langfuse uses OpenTelemetry
  context propagation.  They operate at different layers and do not interfere.

Setup:
  1. Install: ``pip install langsmith``
  2. Set ``LANGCHAIN_API_KEY`` in ``.env`` (from smith.langchain.com)
  3. Set ``LANGCHAIN_TRACING_V2=true`` and ``LANGCHAIN_PROJECT=hypertech``
  4. The tracer is auto-created when ``get_tracer()`` is called — if the API
     key is missing, it returns None and tracing is silently skipped.
"""

import os
import logging
from typing import Optional

from langchain_core.tracers.langchain import LangChainTracer

logger = logging.getLogger(__name__)


def get_tracer() -> Optional[LangChainTracer]:
    """
    Return a LangChainTracer for the current project, or None if LangSmith
    is not configured (missing API key).

    The tracer is created once per call — LangChain's callback system handles
    deduplication and nesting automatically.

    The project name comes from ``LANGCHAIN_PROJECT`` (default: "hypertech").
    Tags can be set via ``LANGCHAIN_TAGS`` (comma-separated).
    """
    api_key = os.getenv("LANGCHAIN_API_KEY")
    if not api_key:
        logger.debug("LangSmith: LANGCHAIN_API_KEY not set — tracing disabled")
        return None

    # LangChainTracer reads LANGCHAIN_API_KEY and LANGCHAIN_ENDPOINT from env
    project = os.getenv("LANGCHAIN_PROJECT", "hypertech")
    tags_raw = os.getenv("LANGCHAIN_TAGS", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else None

    # Enable LangSmith tracing globally so LLM calls outside the graph
    # (e.g. seed_models) also appear in the dashboard.
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")

    return LangChainTracer(
        project_name=project,
        tags=tags,
    )
