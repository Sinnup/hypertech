"""
Agent Registry — decorator-based agent registration with auto-discovery.

Public API:
    register()          — decorator to mark a function as an agent node
    discover_agents()   — import all agents/*/agent.py modules
    get_agents()        — all registered agents
    get_agent(name)     — single agent by name
    get_agent_names()   — sorted list of names
    get_visible_agents()— agents for dashboard display
    register_agent()    — manual registration (safety net)
"""

from core.agent_registry.registry import (
    register,
    discover_agents,
    get_agents,
    get_agent,
    get_agent_names,
    get_visible_agents,
    register_agent,
)

from core.agent_registry.models import (
    AgentOutput,
    AgentDef,
    CONFIDENCE_THRESHOLD_OK,
    CONFIDENCE_THRESHOLD_DEGRADED,
)
