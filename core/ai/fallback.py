"""
Provider fallback — transparently switch providers when the primary fails.

When ``PROVIDER_FALLBACK_ENABLED=true``, every ``get_llm()`` call returns a
wrapper that intercepts ``invoke()`` failures.  If the primary provider
returns an auth error (401), rate limit (429), or timeout, the wrapper
automatically retries with the next provider in the chain.

No agent code changes needed — the wrapper is transparent: same interface,
same return type.

Usage:
    # .env
    PROVIDER_FALLBACK_ENABLED=true
    PROVIDER_FALLBACK_ORDER=claude,deepseek

    # Code (unchanged — fallback is transparent)
    from core.ai.factory import get_llm
    llm = get_llm(ModelTier.BALANCED)  # wrapper with auto-fallback
    result = llm.invoke(...)           # retries on 401/429/timeout
"""

import os
import threading
from typing import Any, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.callbacks import Callbacks

from core.ai.models import ModelTier
from core.secrets.loader import get_optional


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _fallback_enabled() -> bool:
    return get_optional("PROVIDER_FALLBACK_ENABLED", "false").lower() in ("true", "1", "yes")


def _fallback_order() -> list[str]:
    raw = get_optional("PROVIDER_FALLBACK_ORDER", "claude,deepseek")
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _is_fallback_error(error: Exception) -> bool:
    """Return True if *error* warrants trying a different provider.

    Catches: auth failures (401/403), rate limits (429), timeouts, and
    generic connection errors. Does NOT catch validation errors (400) or
    server errors (500) — those would fail on any provider.
    """
    msg = str(error).lower()
    # Anthropic
    if "authenticationerror" in type(error).__name__.lower():
        return True
    if "ratelimiterror" in type(error).__name__.lower():
        return True
    # Generic HTTP errors
    if hasattr(error, "status_code"):
        if error.status_code in (401, 403, 429):
            return True
    # DeepSeek / OpenAI-style errors
    if "401" in msg or "403" in msg or "429" in msg:
        return True
    if "auth" in msg or "quota" in msg or "rate limit" in msg:
        return True
    # Timeout
    if "timeout" in msg or "timed out" in msg:
        return True
    return False


# ---------------------------------------------------------------------------
# Runtime provider override (for /switch-provider Slack command)
# ---------------------------------------------------------------------------

_override_lock = threading.Lock()
_override_provider: Optional[str] = None


def override_provider(provider: str | None):
    """Temporarily set the active provider (thread-safe).

    Used by Slack's ``/switch-provider`` command. Set to ``None`` to clear.
    Updates both the in-memory override AND ``os.environ["AI_PROVIDER"]`` so
    that ``factory.get_provider()`` (which reads the env var directly) also
    picks up the switch.
    """
    global _override_provider
    with _override_lock:
        _override_provider = provider
        if provider:
            os.environ["AI_PROVIDER"] = provider
        else:
            os.environ.pop("AI_PROVIDER", None)


def current_provider() -> str:
    """Return the effective provider, respecting any override."""
    with _override_lock:
        if _override_provider:
            return _override_provider
    return os.getenv("AI_PROVIDER", "claude").strip().lower()


# ---------------------------------------------------------------------------
# Fallback LLM wrapper — intercepts invoke() failures
# ---------------------------------------------------------------------------

class _FallbackChatModel(BaseChatModel):
    """Wraps a chain of provider -> LLM pairs with automatic failover.

    Intercepts ``_generate``, ``_stream``, and ``_agenerate`` so sync,
    streaming, and async invocations all benefit from fallback.

    LLMs are created lazily (only when needed) so the primary provider enjoys
    zero overhead when it succeeds.
    """

    def __init__(
        self,
        chain: list[tuple[str, object]],  # [(provider_name, llm), ...]
        tier: ModelTier,
        temperature: float,
        max_tokens: Optional[int],
    ):
        super().__init__()
        self._chain = chain
        self._tier = tier
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._cache: dict[str, BaseChatModel] = {}

    def _build(self, provider: str) -> BaseChatModel:
        """Create (or return cached) LLM for *provider*."""
        if provider not in self._cache:
            from core.ai.factory import get_llm as _factory_get_llm

            old_provider = os.environ.pop("AI_PROVIDER", None)
            old_fb = os.environ.pop("PROVIDER_FALLBACK_ENABLED", None)
            os.environ["AI_PROVIDER"] = provider
            try:
                self._cache[provider] = _factory_get_llm(
                    self._tier, self._temperature, self._max_tokens
                )
            finally:
                if old_provider is not None:
                    os.environ["AI_PROVIDER"] = old_provider
                else:
                    os.environ.pop("AI_PROVIDER", None)
                if old_fb is not None:
                    os.environ["PROVIDER_FALLBACK_ENABLED"] = old_fb
        return self._cache[provider]

    def _try_generate(self, messages, stop, run_manager, **kwargs):
        """Core fallback loop shared by sync + async paths."""
        errors: list[str] = []
        for i, (provider, _) in enumerate(self._chain):
            try:
                llm = self._build(provider)
                result = llm._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                if i > 0:
                    _notify_fallback(self._chain[0][0], provider, self._tier, errors)
                return result
            except Exception as e:
                if not _is_fallback_error(e):
                    raise
                errors.append(f"{provider}: {e}")
        raise RuntimeError(
            f"All providers failed for tier '{self._tier.value}'. "
            f"Errors: {' | '.join(errors)}"
        )

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._try_generate(messages, stop, run_manager, **kwargs)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        import asyncio
        return await asyncio.to_thread(self._try_generate, messages, stop, run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        """Stream from the primary provider, falling back on failure."""
        errors: list[str] = []
        for i, (provider, _) in enumerate(self._chain):
            try:
                llm = self._build(provider)
                for chunk in llm._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    yield chunk
                if i > 0:
                    _notify_fallback(self._chain[0][0], provider, self._tier, errors)
                return
            except Exception as e:
                if not _is_fallback_error(e):
                    raise
                errors.append(f"{provider}: {e}")
        raise RuntimeError(
            f"All providers failed for tier '{self._tier.value}' (stream). "
            f"Errors: {' | '.join(errors)}"
        )

    @property
    def _llm_type(self) -> str:
        return "fallback-chat-model"

    @property
    def _identifying_params(self) -> dict:
        return {"tier": self._tier.value, "chain": [p for p, _ in self._chain]}


# ---------------------------------------------------------------------------
# Fallback wrapper (public API)
# ---------------------------------------------------------------------------

def get_llm_with_fallback(
    tier: ModelTier,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> BaseChatModel:
    """
    Return an LLM instance for *tier* with automatic provider fallback.

    The returned LLM intercepts ``invoke()`` / ``ainvoke()`` / ``_generate()``
    calls: if the primary provider fails with an auth / rate-limit / timeout
    error, the next provider in ``PROVIDER_FALLBACK_ORDER`` is tried
    transparently.  On success after a fallback, Slack is notified.

    When ``PROVIDER_FALLBACK_ENABLED`` is false, delegates directly to
    ``factory.get_llm()`` with zero overhead.
    """
    from core.ai.factory import get_llm as _get_llm, get_model_id

    if not _fallback_enabled():
        return _get_llm(tier, temperature, max_tokens)

    chain = _fallback_order()
    # Respect /switch-provider overrides: put the active provider first
    # so it's tried before the default fallback order.
    active = current_provider()  # checks _override_provider, then env
    if active in chain and active != chain[0]:
        chain = [active] + [p for p in chain if p != active]

    # Pre-build the primary LLM so normal-case has no extra latency.
    # Fallback providers are built lazily on first failure.
    primary = chain[0]
    # Save/restore AI_PROVIDER so /switch-provider overrides survive pipeline init.
    old_provider = os.environ.pop("AI_PROVIDER", None)
    old_fb = os.environ.pop("PROVIDER_FALLBACK_ENABLED", None)
    os.environ["AI_PROVIDER"] = primary
    try:
        primary_llm = _get_llm(tier, temperature, max_tokens)
    finally:
        # Restore the original provider (or the override set by /switch-provider)
        if old_provider is not None:
            os.environ["AI_PROVIDER"] = old_provider
        else:
            os.environ.pop("AI_PROVIDER", None)
        if old_fb is not None:
            os.environ["PROVIDER_FALLBACK_ENABLED"] = old_fb

    llm_chain: list[tuple[str, BaseChatModel]] = [(primary, primary_llm)]
    for provider in chain[1:]:
        llm_chain.append((provider, None))  # lazy — built on first use

    return _FallbackChatModel(llm_chain, tier, temperature, max_tokens)


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def _notify_fallback(
    from_provider: str,
    to_provider: str,
    tier: ModelTier,
    errors: list[str],
):
    """Post a Slack notification that a provider fallback occurred."""
    try:
        from core.notifications import slack
        from core.ai.factory import get_model_id

        model = get_model_id(tier)
        error_summary = errors[-1] if errors else "unknown error"
        # Truncate long error messages
        if len(error_summary) > 200:
            error_summary = error_summary[:200] + "..."

        slack.alert(
            f"🔄 *Provider Fallback*\n"
            f"*From:* `{from_provider}` → *To:* `{to_provider}`\n"
            f"*Tier:* {tier.value} ({model})\n"
            f"*Reason:* {error_summary}\n"
            f"*Action:* All subsequent calls in this pipeline run will use `{to_provider}`.",
            channel="#pipeline-alerts",
        )
    except Exception:
        pass  # Slack notification failure should never break the pipeline


def notify_health_check():
    """Run on startup — validate all configured provider API keys."""
    from core.ai.factory import get_provider as _factory_get_provider

    providers_to_check = _fallback_order()
    status_lines: list[str] = []

    old_provider = os.environ.pop("AI_PROVIDER", None)
    try:
        for provider in providers_to_check:
            try:
                os.environ["AI_PROVIDER"] = provider
                # Just instantiate — if keys are missing/bad, this will raise
                from core.ai.factory import get_llm as _get_llm
                _get_llm(ModelTier.FAST)
                status_lines.append(f"  ✅ `{provider}` — OK")
            except Exception as e:
                status_lines.append(f"  ❌ `{provider}` — {e}")
    finally:
        if old_provider is not None:
            os.environ["AI_PROVIDER"] = old_provider
        else:
            os.environ.pop("AI_PROVIDER", None)

    try:
        from core.notifications import slack
        slack.alert(
            "🏥 *Provider Health Check*\n" + "\n".join(status_lines),
            channel="#pipeline-alerts",
        )
    except Exception:
        pass  # Slack might not be configured yet
