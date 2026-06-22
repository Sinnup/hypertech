"""
Checkpoint module — persistent PipelineState snapshots for crash recovery.

Usage::

    from core.checkpoint import save, load, delete, exists

    # After each agent cycle completes:
    save(ticket_id, state)

    # On pipeline restart:
    checkpoint = load(ticket_id)
    if checkpoint:
        state = checkpoint  # resumed_from_checkpoint == True

    # On successful completion:
    delete(ticket_id)
"""

from core.checkpoint.manager import save, load, delete, exists, list_checkpoints

__all__ = ["save", "load", "delete", "exists", "list_checkpoints"]
