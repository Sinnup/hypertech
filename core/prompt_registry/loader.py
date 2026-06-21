"""
Prompt Registry — version-controlled prompts loaded from the ``prompts/`` directory.

Layout::

    prompts/
      {agent_name}/
        system.txt       # main system prompt
        planning.txt     # (orchestrator only) dynamic planning instructions
        ...

Usage::

    from core.prompt_registry import get_prompt
    system = get_prompt("coder", "system")
"""

from __future__ import annotations

import functools
from pathlib import Path

# ---------------------------------------------------------------------------
# Prompt directory
# ---------------------------------------------------------------------------

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _read(path: Path) -> str:
    """Read a file with caching — avoids re-reading the same prompt on every agent run."""
    return path.read_text().strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_prompt(agent: str, prompt_type: str = "system") -> str:
    """Load a prompt from the filesystem.

    Args:
        agent: Agent name (e.g. ``"coder"``, ``"orchestrator"``).
        prompt_type: Prompt file stem (default ``"system"``).

    Returns:
        The prompt text, or an empty string if not found.
    """
    prompt_path = _PROMPT_DIR / agent / f"{prompt_type}.txt"
    if prompt_path.exists():
        return _read(prompt_path)
    return ""


def list_prompts(agent: str) -> list[str]:
    """List available prompt types for an agent (e.g. ``["system"]``)."""
    agent_dir = _PROMPT_DIR / agent
    if not agent_dir.is_dir():
        return []
    return sorted(p.stem for p in agent_dir.glob("*.txt"))


def list_agents_with_prompts() -> list[str]:
    """Return agent names that have a prompts/ subdirectory."""
    if not _PROMPT_DIR.is_dir():
        return []
    return sorted(
        p.name for p in _PROMPT_DIR.iterdir() if p.is_dir() and list(p.glob("*.txt"))
    )
