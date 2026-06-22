"""
Checkpoint manager — save/load/delete PipelineState snapshots with FileLock.

Provides thread/process-safe checkpoint persistence using the same
FileLock pattern as core/registry.py.  Each ticket gets its own
checkpoint file and lock, so concurrent pipelines for different tickets
never contend.

Atomic writes: state is written to a .tmp file first, then renamed
(atomic on POSIX).  A partial write never lands on the .json file.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock

from core.secrets.loader import get_optional

_CHECKPOINT_DIR = Path(get_optional("CHECKPOINT_DIR", ".hypertech/checkpoints"))
_CHECKPOINT_VERSION = 1

logger = logging.getLogger(__name__)


def _lock_for(ticket_id: str) -> FileLock:
    """Return a FileLock scoped to a single ticket's checkpoint."""
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _CHECKPOINT_DIR / f"{ticket_id}.lock"
    return FileLock(str(lock_path), timeout=15)


def _path_for(ticket_id: str) -> Path:
    return _CHECKPOINT_DIR / f"{ticket_id}.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save(ticket_id: str, state: dict) -> None:
    """
    Atomically save a PipelineState checkpoint to disk.

    Uses atomic-write pattern (write to .tmp, then rename) so partial
    writes never leave a corrupt checkpoint file behind.
    """
    snapshot = {
        "ticket_id": ticket_id,
        "version": _CHECKPOINT_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "completed_agents": list(state.get("agent_outputs", {}).keys()),
        "agent_plan_index": state.get("agent_plan_index", 0),
        "state": state,
    }

    path = _path_for(ticket_id)
    tmp = path.with_suffix(".tmp")

    with _lock_for(ticket_id):
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(snapshot, indent=2, default=str))
        tmp.rename(path)  # atomic on POSIX

    logger.info(
        "Checkpoint saved for %s (plan_index=%s, completed=%s)",
        ticket_id,
        snapshot["agent_plan_index"],
        snapshot["completed_agents"],
    )


def load(ticket_id: str) -> Optional[dict]:
    """
    Load a checkpoint for *ticket_id*.

    Returns the PipelineState dict (with ``resumed_from_checkpoint``
    injected as ``True``) or *None* if no valid checkpoint exists.

    Corruption handling: if the file cannot be parsed as JSON, logs a
    warning and returns None (effectively a fresh start).
    """
    path = _path_for(ticket_id)
    if not path.exists():
        return None

    with _lock_for(ticket_id):
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Corrupted checkpoint for %s: %s — starting fresh", ticket_id, exc
            )
            return None

    state = raw.get("state")
    if not isinstance(state, dict):
        logger.warning(
            "Invalid checkpoint for %s: missing state — starting fresh", ticket_id
        )
        return None

    # Inject resume flag so the orchestrator can detect resume mode.
    state["resumed_from_checkpoint"] = True

    logger.info(
        "Checkpoint loaded for %s (plan_index=%s, completed=%s)",
        ticket_id,
        state.get("agent_plan_index"),
        list(state.get("agent_outputs", {}).keys()),
    )
    return state


def delete(ticket_id: str) -> None:
    """Remove the checkpoint file and its lock for *ticket_id* (if they exist)."""
    path = _path_for(ticket_id)
    lock_path = _CHECKPOINT_DIR / f"{ticket_id}.lock"

    with _lock_for(ticket_id):
        if path.exists():
            path.unlink()
        if lock_path.exists():
            lock_path.unlink()

    logger.info("Checkpoint deleted for %s", ticket_id)


def exists(ticket_id: str) -> bool:
    """Return ``True`` if a checkpoint file exists for *ticket_id*."""
    return _path_for(ticket_id).exists()


def list_checkpoints() -> list[dict]:
    """
    Return metadata for all existing checkpoints, newest first.

    Each entry::

        {
            "ticket_id": str,
            "saved_at": str,
            "agent_plan_index": int,
            "completed_agents": [str, ...],
        }

    Corrupted files are silently skipped.
    """
    results: list[dict] = []
    if not _CHECKPOINT_DIR.exists():
        return results

    for path in sorted(_CHECKPOINT_DIR.glob("*.json"), reverse=True):
        try:
            raw = json.loads(path.read_text())
            results.append({
                "ticket_id": raw.get("ticket_id", path.stem),
                "saved_at": raw.get("saved_at", ""),
                "agent_plan_index": raw.get("agent_plan_index", 0),
                "completed_agents": raw.get("completed_agents", []),
            })
        except (json.JSONDecodeError, OSError):
            logger.debug("Skipping unreadable checkpoint: %s", path)
            continue

    # Sort by saved_at descending so most recent is first
    return sorted(results, key=lambda x: x["saved_at"], reverse=True)
