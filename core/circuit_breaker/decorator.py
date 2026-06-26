"""
Circuit breaker decorator for external service calls.

Wraps any function that calls an external service (GitHub, Figma, ChromaDB, etc.)
with timeout, fallback, and optional Slack approval gating.

Usage::

    from core.circuit_breaker import circuit_breaker

    @circuit_breaker(
        service_name="github_api",
        fallback=_save_locally,
        fallback_label="Save locally and defer GitHub commit",
        timeout=15.0,
    )
    def commit_to_github(token, repo, ...):
        ...
"""

from __future__ import annotations

import concurrent.futures
import functools
import threading
import time
from typing import Callable, Optional

from core.tracing.langfuse import observe

from core.circuit_breaker.models import CircuitBreakerResult
from core.notifications import slack
from core.tracing.langfuse import get_client


class CircuitBreakerError(RuntimeError):
    """Raised when all paths (primary + fallback) fail, or fallback is rejected."""

    def __init__(self, service: str, reason: str):
        super().__init__(f"[{service}] {reason}")
        self.service = service
        self.reason = reason


# ---------------------------------------------------------------------------
# Decorator factory
# ---------------------------------------------------------------------------

def circuit_breaker(
    service_name: str,
    *,
    fallback: Optional[Callable] = None,
    fallback_label: str = "",
    timeout: float = 30.0,
    require_approval: bool = True,
):
    """Wrap a function with circuit breaker semantics.

    Args:
        service_name: Human-readable name for Slack messages and traces.
        fallback: Function to call if primary fails.  Must accept the same
            args/kwargs as the wrapped function, OR a subset.
        fallback_label: What to tell the human when asking for fallback approval.
        timeout: Seconds before the primary call is considered failed.
        require_approval: If True, send a Slack approval request before
            activating the fallback.  If False, fall back immediately.
    """

    def decorator(func: Callable) -> Callable:

        @functools.wraps(func)
        @observe(name=f"cb-{service_name}")
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            result = CircuitBreakerResult(service=service_name)

            # === Primary path ===
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(func, *args, **kwargs)
                    ret = future.result(timeout=timeout)

                result.status = "primary_ok"
                result.latency_ms = (time.monotonic() - t0) * 1000
                return ret

            except concurrent.futures.TimeoutError:
                result.primary_error = f"Timeout after {timeout}s"
            except Exception as exc:
                result.primary_error = str(exc)

            # === Primary failed — record in Langfuse ===
            client = get_client()
            if client:
                client.update_current_span(
                    metadata={
                        "circuit_breaker": service_name,
                        "primary_error": result.primary_error,
                    }
                )

            # === No fallback configured ===
            if fallback is None:
                result.status = "failed"
                result.latency_ms = (time.monotonic() - t0) * 1000
                raise CircuitBreakerError(
                    service_name,
                    f"Primary failed ({result.primary_error}) and no fallback configured.",
                )

            # === Fallback exists — may need approval ===
            if require_approval:
                result.approval_requested = True
                approved = _request_fallback_approval(service_name, fallback_label, result.primary_error)
                result.approval_granted = approved
                if not approved:
                    result.status = "rejected"
                    result.latency_ms = (time.monotonic() - t0) * 1000
                    raise CircuitBreakerError(
                        service_name,
                        f"Fallback to '{fallback_label}' was rejected by human.",
                    )

            # === Execute fallback ===
            try:
                ret = fallback(*args, **kwargs)
                result.status = "fallback_used"
                result.latency_ms = (time.monotonic() - t0) * 1000

                _notify_fallback(service_name, fallback_label, result.primary_error)
                return ret

            except Exception as exc:
                result.status = "failed"
                result.fallback_error = str(exc)
                result.latency_ms = (time.monotonic() - t0) * 1000
                raise CircuitBreakerError(
                    service_name,
                    f"Both primary and fallback failed. Primary: {result.primary_error}. "
                    f"Fallback: {result.fallback_error}",
                )

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Slack approval for fallback activation
# ---------------------------------------------------------------------------

def _request_fallback_approval(
    service: str,
    fallback_label: str,
    error: str,
) -> bool:
    """Send a Slack approval request and wait for a response.

    Posts an interactive message with Approve/Reject buttons, then blocks
    on a threading.Event that the Slack interactive handler sets when the
    human clicks a button.  Falls back to auto-approve after 120 s if no
    response is received (demo mode: don't block forever).
    """
    from core.notifications.approval_store import register_approval_event, get_approval_result

    ticket_id = f"CB-{service}-{id(threading.current_thread()) % 10000}"

    # Build a readable message and send the approval request
    summary = (
        f"*Service:* `{service}`\n"
        f"*Error:* {error[:200]}\n"
        f"*Fallback:* {fallback_label}\n\n"
        f"Click ✅ Approve to activate the fallback, or ❌ Reject to abort."
    )

    slack.approval_request(
        ticket_id=ticket_id,
        stage=f"circuit_breaker_{service}",
        summary=summary,
    )

    # Register an event and wait
    stage = f"circuit_breaker_{service}"
    evt = register_approval_event(ticket_id, stage)

    # Wait up to 120 s for human response
    approved = evt.wait(timeout=120.0)
    if not approved:
        # Timeout — auto-approve for demo resilience
        slack.alert(
            f"⏰ Circuit breaker `{service}` approval timed out after 120 s — "
            f"auto-approving fallback to *{fallback_label}*."
        )
        return True

    # Check the result
    result = get_approval_result(ticket_id, stage)
    if result and result.get("status") == "approved":
        return True

    return False


def _notify_fallback(service: str, fallback_label: str, error: str) -> None:
    """Post a notification that fallback was activated."""
    slack.alert(
        f"⚠️ Circuit breaker activated: `{service}` fell back to *{fallback_label}*\n"
        f"Primary error: {error[:150]}"
    )
