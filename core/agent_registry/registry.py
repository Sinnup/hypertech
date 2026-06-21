"""
Agent registry — decorator-based registration and auto-discovery.

Usage in any agent module:

    from core.agent_registry import register

    @register("my_agent", description="Does something", tier=ModelTier.BALANCED)
    @observe(name="my-agent")
    def run(state: PipelineState) -> PipelineState:
        ...

The registry is populated lazily — call ``discover_agents()`` to import
all agent modules under ``agents/`` and fire their ``@register`` decorators.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from core.agent_registry.models import AgentDef
from core.ai.models import ModelTier

# ---------------------------------------------------------------------------
# In-memory registry
# ---------------------------------------------------------------------------

_registry: dict[str, AgentDef] = {}
_discovered: bool = False


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

def register(
    name: str,
    *,
    description: str = "",
    tier: ModelTier = ModelTier.BALANCED,
    tags: list[str] | None = None,
    visible: bool = True,
) -> Callable:
    """Decorator — register a function as an agent node."""

    def decorator(func):
        _registry[name] = AgentDef(
            name=name,
            description=description,
            tier=tier,
            tags=tags or [],
            fn=func,
            visible=visible,
        )
        return func

    return decorator


# ---------------------------------------------------------------------------
# Discovery — import all agent packages so @register fires
# ---------------------------------------------------------------------------

def discover_agents() -> None:
    """Auto-import every ``agents/*/agent.py`` module so ``@register`` decorators fire.

    Idempotent — subsequent calls are no-ops.
    """
    global _discovered
    if _discovered:
        return

    import agents  # the top-level package

    for _, modname, ispkg in pkgutil.iter_modules(agents.__path__):
        if ispkg:
            try:
                importlib.import_module(f"agents.{modname}.agent")
            except Exception:
                # Agent module failed to import — skip it rather than crashing
                # the whole discovery.  The error will surface when the pipeline
                # actually tries to route to that agent.
                pass

    _discovered = True


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

def get_agents() -> dict[str, AgentDef]:
    """Return all registered agents (name → AgentDef)."""
    return dict(_registry)


def get_agent(name: str) -> AgentDef | None:
    """Return a single agent by name, or None."""
    return _registry.get(name)


def get_agent_names() -> list[str]:
    """Return sorted list of registered agent names."""
    return sorted(_registry.keys())


def get_visible_agents() -> dict[str, AgentDef]:
    """Return agents that should appear in the dashboard (visible=True)."""
    return {k: v for k, v in _registry.items() if v.visible}


def register_agent(name: str, fn, **kwargs) -> None:
    """Manually register an agent (safety net if auto-discovery misses one)."""
    _registry[name] = AgentDef(name=name, fn=fn, **kwargs)
