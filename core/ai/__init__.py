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

__all__ = [
    "get_llm",
    "get_model_id",
    "get_provider",
    "ModelTier",
    "parse_json",
    "strip_fences",
]
