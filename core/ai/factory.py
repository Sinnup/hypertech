"""
AI provider factory — selects and constructs the right LLM for the active
provider at runtime, keyed off the ``AI_PROVIDER`` env var.

Default is ``"claude"`` so existing deployments are unchanged.
"""

import os
from typing import Optional

from langchain_core.language_models import BaseChatModel

from core.ai.models import ModelTier, REGISTRY
from core.secrets.loader import get


def get_provider() -> str:
    """Return the active provider name.  Defaults to ``"claude"``."""
    return os.getenv("AI_PROVIDER", "claude").strip().lower()


def get_model_id(tier: ModelTier) -> str:
    """Resolve a tier to a provider-specific model ID string.

    Useful for Langfuse tracing so the recorded model name always matches
    the provider actually in use.
    """
    provider = get_provider()
    try:
        return REGISTRY[provider][tier.value]
    except KeyError:
        raise ValueError(
            f"No model for tier='{tier.value}' under provider='{provider}'. "
            f"Available providers: {list(REGISTRY)}"
        )


def get_llm(
    tier: ModelTier,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> BaseChatModel:
    """
    Create an LLM instance for *tier* using the active provider.

    When ``AI_PROVIDER=deepseek`` and *tier* is ``POWERFUL``, *temperature*
    is intentionally not forwarded — deepseek-reasoner rejects it.
    """
    provider = get_provider()
    model_id = get_model_id(tier)

    if provider == "claude":
        return _build_claude(tier, model_id, temperature, max_tokens)
    elif provider == "deepseek":
        return _build_deepseek(tier, model_id, temperature, max_tokens)
    else:
        raise ValueError(
            f"Unsupported provider '{provider}'. "
            f"Valid choices: {list(REGISTRY)}"
        )


# ---------------------------------------------------------------------------
# Provider builders (private)
# ---------------------------------------------------------------------------

def _build_claude(
    tier: ModelTier,
    model_id: str,
    temperature: float,
    max_tokens: Optional[int],
) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    kwargs: dict = {
        "model": model_id,
        "anthropic_api_key": get("ANTHROPIC_API_KEY"),
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatAnthropic(**kwargs)


def _build_deepseek(
    tier: ModelTier,
    model_id: str,
    temperature: float,
    max_tokens: Optional[int],
) -> BaseChatModel:
    from langchain_deepseek import ChatDeepSeek

    kwargs: dict = {
        "model": model_id,
        "api_key": get("DEEPSEEK_API_KEY"),
    }
    # deepseek-reasoner does not support temperature
    if tier != ModelTier.POWERFUL:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatDeepSeek(**kwargs)
