"""
e2e test environment setup.

Disable OTEL/Langfuse/LangSmith network exporters so unit-level tests that touch
``@observe``-decorated nodes don't block on a 5s export timeout to localhost.
Set at import time (before any langfuse client initialises).
"""

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")
