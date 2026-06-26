"""
Shared in-process store for human-in-the-loop approvals.

This lives in its OWN module (not in ``slack_commands``) on purpose. The Slack
server is started with ``python -m core.notifications.slack_commands``, which
loads that file as the ``__main__`` module. Globals defined in ``__main__`` are
NOT the same objects seen by code that does
``from core.notifications.slack_commands import ...`` (Python creates a second
module object under the real dotted name). That split meant approvals stored by
the Slack interactive handler were invisible to the pipeline's validation gate —
so Slack approvals never resumed the pipeline.

Keeping the dicts here guarantees ONE shared instance regardless of how the
server is launched: both the Slack handler and the validation gate import this
module under the same dotted name.
"""

import threading

# Keyed by f"{ticket_id}|{stage}".
_approval_events: dict[str, threading.Event] = {}
_approval_results: dict[str, dict] = {}


def _approval_key(ticket_id: str, stage: str) -> str:
    return f"{ticket_id}|{stage}"


def register_approval_event(ticket_id: str, stage: str) -> threading.Event:
    """Create an Event the circuit breaker can wait on; set when a response arrives."""
    evt = threading.Event()
    _approval_events[_approval_key(ticket_id, stage)] = evt
    return evt


def get_approval_result(ticket_id: str, stage: str) -> dict | None:
    """Return the stored approval result dict, or None if not yet received."""
    return _approval_results.get(_approval_key(ticket_id, stage))


def store_approval(ticket_id: str, stage: str, result: dict) -> None:
    """Store an approval result and signal any waiter for this (ticket, stage)."""
    key = _approval_key(ticket_id, stage)
    _approval_results[key] = result
    evt = _approval_events.get(key)
    if evt:
        evt.set()
