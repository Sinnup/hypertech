"""
Unit tests for /switch-provider → get_llm behavior.

Covers the three bugs fixed in the provider switch flow:
  1. override_provider() must update os.environ so factory.get_provider() sees it
  2. get_llm_with_fallback() must save/restore AI_PROVIDER so the override survives
  3. get_llm_with_fallback() must reorder the chain so the active provider is primary
"""

import os
import pytest

from core.ai.fallback import override_provider, current_provider, get_llm_with_fallback
from core.ai.factory import get_provider
from core.ai.models import ModelTier


@pytest.fixture(autouse=True)
def _isolate_env():
    """Save and restore environment so tests don't leak state."""
    saved = {
        "AI_PROVIDER": os.environ.get("AI_PROVIDER"),
        "PROVIDER_FALLBACK_ENABLED": os.environ.get("PROVIDER_FALLBACK_ENABLED"),
        "PROVIDER_FALLBACK_ORDER": os.environ.get("PROVIDER_FALLBACK_ORDER"),
    }
    # Set known defaults for every test
    os.environ["AI_PROVIDER"] = "claude"
    os.environ["PROVIDER_FALLBACK_ENABLED"] = "true"
    os.environ["PROVIDER_FALLBACK_ORDER"] = "claude,deepseek"
    override_provider(None)  # clear any override from previous test
    yield
    # Restore
    override_provider(None)
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# ---------------------------------------------------------------------------
# Fix #1: override_provider updates os.environ
# ---------------------------------------------------------------------------

def test_switch_provider_sets_env_var():
    """override_provider('deepseek') sets AI_PROVIDER in os.environ."""
    override_provider("deepseek")
    assert os.environ["AI_PROVIDER"] == "deepseek"


def test_switch_provider_factory_sees_override():
    """factory.get_provider() returns the override, not .env default."""
    override_provider("deepseek")
    assert get_provider() == "deepseek"


def test_switch_provider_current_provider_sees_override():
    """current_provider() returns the override."""
    override_provider("deepseek")
    assert current_provider() == "deepseek"


def test_switch_provider_clear_pops_env():
    """Clearing the override pops AI_PROVIDER so the hardcoded default 'claude' applies."""
    os.environ["AI_PROVIDER"] = "deepseek"
    override_provider(None)
    # override_provider(None) pops the env var, so current_provider()
    # falls back to os.getenv("AI_PROVIDER", "claude") → "claude"
    assert "AI_PROVIDER" not in os.environ
    assert current_provider() == "claude"


# ---------------------------------------------------------------------------
# Fix #2: get_llm_with_fallback preserves the override
# ---------------------------------------------------------------------------

def test_get_llm_preserves_override():
    """After get_llm_with_fallback(), AI_PROVIDER is still the override."""
    override_provider("deepseek")
    get_llm_with_fallback(ModelTier.FAST)
    assert os.environ["AI_PROVIDER"] == "deepseek"


def test_get_llm_preserves_current_provider():
    """After get_llm_with_fallback(), current_provider() still returns override."""
    override_provider("deepseek")
    get_llm_with_fallback(ModelTier.FAST)
    assert current_provider() == "deepseek"


def test_get_llm_preserves_fallback_enabled():
    """PROVIDER_FALLBACK_ENABLED survives get_llm_with_fallback()."""
    os.environ["PROVIDER_FALLBACK_ENABLED"] = "true"
    get_llm_with_fallback(ModelTier.FAST)
    assert os.environ["PROVIDER_FALLBACK_ENABLED"] == "true"


# ---------------------------------------------------------------------------
# Fix #3: active provider becomes primary in fallback chain
# ---------------------------------------------------------------------------

def test_switch_provider_makes_deepseek_primary():
    """After /switch-provider deepseek, the fallback chain starts with deepseek."""
    override_provider("deepseek")
    llm = get_llm_with_fallback(ModelTier.FAST)
    chain = [p for p, _ in llm._chain]
    assert chain[0] == "deepseek", f"Expected deepseek first, got {chain}"
    assert "claude" in chain, f"Claude should still be in the chain: {chain}"


def test_default_chain_claude_primary():
    """Without override, claude remains primary."""
    llm = get_llm_with_fallback(ModelTier.FAST)
    chain = [p for p, _ in llm._chain]
    assert chain[0] == "claude", f"Expected claude first, got {chain}"


def test_switch_back_to_claude():
    """Switching back to claude makes it primary again."""
    override_provider("deepseek")
    override_provider("claude")
    llm = get_llm_with_fallback(ModelTier.FAST)
    assert llm._chain[0][0] == "claude"


def test_chain_contains_both_providers():
    """Fallback chain always contains both providers."""
    llm = get_llm_with_fallback(ModelTier.FAST)
    providers = {p for p, _ in llm._chain}
    assert providers == {"claude", "deepseek"}


# ---------------------------------------------------------------------------
# Full integration: simulation of Slack commands
# ---------------------------------------------------------------------------

def test_full_flow_switch_then_new():
    """
    Simulate the full Slack flow:
      /switch-provider deepseek
      /new "prompt" → get_llm() → primary is deepseek
    """
    # Step 1: /switch-provider deepseek
    override_provider("deepseek")
    assert os.environ["AI_PROVIDER"] == "deepseek"

    # Step 2: /new spawns thread → run_pipeline() → get_llm()
    llm = get_llm_with_fallback(ModelTier.BALANCED, temperature=0.3)

    # Step 3: DeepSeek is the primary
    assert llm._chain[0][0] == "deepseek"

    # Step 4: The override survived
    assert os.environ["AI_PROVIDER"] == "deepseek"
    assert current_provider() == "deepseek"


def test_fallback_disabled_uses_direct_provider():
    """When fallback is disabled, get_llm uses the active provider directly."""
    os.environ["PROVIDER_FALLBACK_ENABLED"] = "false"
    override_provider("deepseek")

    from core.ai.factory import get_llm
    llm = get_llm(ModelTier.FAST)

    # Should be a direct ChatDeepSeek, not a FallbackChatModel
    type_name = type(llm).__name__
    assert "DeepSeek" in type_name, f"Expected DeepSeek LLM, got {type_name}"
