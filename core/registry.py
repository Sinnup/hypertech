"""
Feature registry — thread/process-safe read and write helpers.

All agents must use these functions instead of rolling their own
read-modify-write pattern. A FileLock beside the JSON file prevents
concurrent pipeline runs from overwriting each other's updates.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from core.secrets.loader import get_optional

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))
_LOCK_PATH = _REGISTRY_PATH.with_suffix(".lock")


def _lock() -> FileLock:
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(_LOCK_PATH), timeout=15)


def load() -> dict:
    """Return the full registry dict (empty dict if file not found)."""
    with _lock():
        if _REGISTRY_PATH.exists():
            return json.loads(_REGISTRY_PATH.read_text())
        return {}


def save(registry: dict) -> None:
    """Atomically overwrite the registry file."""
    with _lock():
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def update_ticket(ticket_id: str, updates: dict) -> None:
    """
    Merge `updates` into an existing ticket entry.
    No-op if the ticket_id is not already in the registry.
    Always stamps `last_updated`.
    """
    with _lock():
        registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
        if ticket_id not in registry:
            return
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def create_ticket(ticket_id: str, entry: dict) -> None:
    """
    Insert a new ticket entry (or overwrite if it already exists).
    Stamps `last_updated`.
    """
    with _lock():
        registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
        entry["last_updated"] = datetime.now(timezone.utc).isoformat()
        registry[ticket_id] = entry
        _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
