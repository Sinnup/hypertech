"""
AI provider abstraction — single import surface for all agents.

Usage::

    from core.ai import get_llm, get_model_id, parse_json, strip_fences, ModelTier

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.3)
    result = llm.invoke(...)
    data = parse_json(result.content)
"""

from core.ai.factory import get_llm, get_model_id, get_provider
from core.ai.models import ModelTier
from core.ai.parsing import parse_json, strip_fences
from core.ai.fallback import (
    get_llm_with_fallback,
    override_provider,
    current_provider,
    notify_health_check,
)

__all__ = [
    "get_llm",
    "get_llm_with_fallback",
    "get_model_id",
    "get_provider",
    "override_provider",
    "current_provider",
    "notify_health_check",
    "ModelTier",
    "parse_json",
    "strip_fences",
]
