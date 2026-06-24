"""
LangSmith tracing — automatic for LangChain/LangGraph apps.

Why alongside Langfuse:
  Langfuse sees the pipeline as a flat OTEL span tree — it has no concept of
  LangGraph nodes, edges, state transitions, or DAG structure.  LangSmith's
  native LangGraph integration captures:

  - Graph DAG structure and topology
  - Node execution order with timing
  - Edge transitions (which node → which node)
  - State diffs between nodes
  - LLM calls grouped per-node (prompt + response)
  - Token usage with cost per node
  - Conditional edge routing
  - Recursion/loop visualization

How it works:
  For LangChain/LangGraph apps, tracing is **automatic**.  Just set the
  environment variables below — the LangSmith SDK auto-detects LangGraph
  usage and installs its TracingCallbackHandler.  No manual callback needed.

  This is the pattern prescribed by the ``langsmith-trace`` skill
  (``<trace_langchain_oss>`` section).

Coexistence:
  LangSmith uses LangChain's callback system.  Langfuse uses OpenTelemetry
  context propagation.  They operate at different layers and do not interfere.

Setup:
  1. Install: ``pip install langsmith``
  2. Set env vars in ``.env``:
     LANGSMITH_TRACING=true
     LANGSMITH_API_KEY=lsv2_pt_...
     LANGSMITH_PROJECT=hypertech
  3. That's it — traces appear at smith.langchain.com

Querying (langsmith CLI):
  curl -sSL https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh | sh
  langsmith trace list --limit 10 --project hypertech
"""

import os
import logging

logger = logging.getLogger(__name__)

# LangSmith auto-detection env vars (preferred naming per langsmith-trace skill)
_AUTO_VARS = {
    "LANGSMITH_TRACING": os.getenv("LANGSMITH_TRACING"),
    "LANGSMITH_API_KEY": os.getenv("LANGSMITH_API_KEY"),
    "LANGSMITH_PROJECT": os.getenv("LANGSMITH_PROJECT", "hypertech"),
}


def is_configured() -> bool:
    """Return True if LangSmith auto-tracing env vars are set."""
    return bool(_AUTO_VARS["LANGSMITH_TRACING"] and _AUTO_VARS["LANGSMITH_API_KEY"])


def ensure():
    """
    Ensure LangSmith environment is configured for auto-tracing.

    Called once at startup — sets defaults and validates configuration.
    Logs a warning if LANGSMITH_TRACING is set but LANGSMITH_API_KEY is missing.
    """
    # Set LANGSMITH_TRACING if the legacy LANGCHAIN_TRACING_V2 is set
    if not os.getenv("LANGSMITH_TRACING") and os.getenv("LANGCHAIN_TRACING_V2"):
        os.environ["LANGSMITH_TRACING"] = "true"

    # Copy LANGCHAIN_API_KEY → LANGSMITH_API_KEY if latter is unset
    if not os.getenv("LANGSMITH_API_KEY") and os.getenv("LANGCHAIN_API_KEY"):
        os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGCHAIN_API_KEY", "")

    # Set default project if missing
    os.environ.setdefault("LANGSMITH_PROJECT", "hypertech")

    if not is_configured():
        logger.info(
            "LangSmith: auto-tracing disabled — set LANGSMITH_TRACING=true and "
            "LANGSMITH_API_KEY in .env"
        )
    else:
        logger.info(
            "LangSmith: auto-tracing enabled — project=%s",
            os.getenv("LANGSMITH_PROJECT"),
        )
