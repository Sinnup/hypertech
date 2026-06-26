"""
Unified progress reporting — one call updates Slack AND the /viz dashboard.

Agents already sprinkle ``slack.status(ticket_id, "…")`` to report what they're
doing. :func:`report_step` does the same Slack post AND emits a viz
``agent_progress`` substep, so every existing report becomes a real-time
sub-state in the visualizer with a single-line change.
"""

from core.notifications import slack
from core.events.graph_events import emit_progress


def report_step(ticket_id: str, agent: str, message: str, detail: str | None = None) -> None:
    """Post *message* to Slack and emit it as a viz substep for *agent*."""
    try:
        slack.status(ticket_id, message)
    except Exception:  # noqa: BLE001 — never let reporting break an agent
        pass
    try:
        emit_progress(ticket_id, agent, message, detail)
    except Exception:  # noqa: BLE001
        pass
