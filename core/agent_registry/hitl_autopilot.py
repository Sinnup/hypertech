"""
HitL Autopilot — deterministic human-in-the-loop responses for testing.

In normal operation, when the validation gate escalates (low confidence or an
agent question), the pipeline posts a Slack approval request and waits for a
human button click.  For automated end-to-end tests we need to drive those
decisions deterministically, without Slack.

Set the ``HITL_AUTOPILOT`` env var to one of:

    approve   every escalation is auto-approved → pipeline continues
    reject    every escalation is auto-rejected → pipeline fails (clean stop)
    changes   first escalation per stage requests changes, then approves
              (simulates "human asked for a modification, then accepted")
    idle      (or unset) → no injection; real human escalation as in production

This is a *test affordance only*: when the env var is unset or ``idle`` the
module is a complete no-op, so it is safe to leave wired into production code.
The validation gate calls :func:`decision` as the final fallback in its
approval lookup chain (after the real in-memory Slack approval check).
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MODE_ENV = "HITL_AUTOPILOT"
_VALID = {"approve", "reject", "changes", "idle"}

# Per-(ticket, stage) injection counter so "changes" can flip to approve on the
# second evaluation (after a resume), simulating a modify-then-accept cycle.
_injections: dict[tuple[str, str], int] = {}


def mode() -> str:
    """Return the current autopilot mode (lower-cased), or '' if disabled."""
    m = os.getenv(_MODE_ENV, "").strip().lower()
    if m and m not in _VALID:
        logger.warning("Unknown HITL_AUTOPILOT=%r — treating as idle", m)
        return ""
    return "" if m == "idle" else m


def enabled() -> bool:
    return mode() != ""


def reset() -> None:
    """Clear injection state — call between independent test runs."""
    _injections.clear()


def _mk(stage: str, status: str, comment: str) -> dict:
    return {
        "stage": stage,
        "status": status,
        "approved_by": "autopilot",
        "comment": comment,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def decision(ticket_id: str, stage: str) -> dict | None:
    """Return a synthetic approval dict for *ticket_id*/*stage*, or None.

    None means "no autopilot decision" — the gate then escalates to a human,
    exactly as in production.  Shape matches the Slack interactive handler's
    stored approval result so the gate's existing approve/reject/changes logic
    handles it unchanged.
    """
    m = mode()
    if not m:
        return None

    key = (ticket_id, stage)
    n = _injections.get(key, 0)
    _injections[key] = n + 1

    if m == "approve":
        return _mk(stage, "approved", "auto-approved (HITL_AUTOPILOT=approve)")
    if m == "reject":
        return _mk(stage, "rejected", "auto-rejected (HITL_AUTOPILOT=reject)")
    if m == "changes":
        if n == 0:
            return _mk(stage, "changes_requested",
                       "auto: please tighten the implementation (HITL_AUTOPILOT=changes)")
        return _mk(stage, "approved", "auto-approved after changes (HITL_AUTOPILOT=changes)")
    return None
