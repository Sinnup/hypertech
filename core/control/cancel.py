"""
Stop / cancel control for running pipelines and commands.

A single shared store (its own module, like ``core/notifications/approval_store``)
plus a file flag for cross-process cancellation: a pipeline launched from the
Slack server runs in that process (in-memory set suffices), but a CLI run
(``python main.py``) is a different process — the ``.hypertech/cancel/{ticket}``
flag bridges them.

``stream_pipeline`` checks :func:`is_stop_requested` after each node and halts
gracefully at the next boundary.
"""

import logging
from pathlib import Path

from core.secrets.loader import get_optional

logger = logging.getLogger(__name__)

_CANCEL_DIR = Path(get_optional("CANCEL_DIR", ".hypertech/cancel"))
_requested: set[str] = set()


def _flag(ticket_id: str) -> Path:
    return _CANCEL_DIR / f"{ticket_id}.flag"


def request_stop(ticket_id: str) -> None:
    """Mark *ticket_id* for cancellation (in-memory + file flag)."""
    _requested.add(ticket_id)
    try:
        _CANCEL_DIR.mkdir(parents=True, exist_ok=True)
        _flag(ticket_id).write_text("stop")
    except Exception:  # noqa: BLE001 — in-memory set still works
        logger.debug("Could not write cancel flag for %s", ticket_id, exc_info=True)


def is_stop_requested(ticket_id: str) -> bool:
    """True if a stop was requested for *ticket_id* (this process or any other)."""
    if ticket_id in _requested:
        return True
    try:
        return _flag(ticket_id).exists()
    except Exception:  # noqa: BLE001
        return False


def clear_stop(ticket_id: str) -> None:
    """Clear the cancellation flag (call once the run has halted)."""
    _requested.discard(ticket_id)
    try:
        f = _flag(ticket_id)
        if f.exists():
            f.unlink()
    except Exception:  # noqa: BLE001
        pass
