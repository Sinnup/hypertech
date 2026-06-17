"""
Model registry — tier enum and provider-to-model-ID mapping.

To add a new provider (e.g. OpenAI, Gemini), add one dict entry to REGISTRY
and create a builder in factory.py.  No other files need to change.
"""

from enum import Enum


class ModelTier(str, Enum):
    """Logical model tiers — callers use these, not raw model IDs."""

    FAST = "fast"          # Classification, simple reviews (cheapest)
    BALANCED = "balanced"  # Most generation tasks
    POWERFUL = "powerful"  # Deep architectural reasoning


# provider → tier → provider-specific model ID
REGISTRY: dict[str, dict[str, str]] = {
    "claude": {
        "fast": "claude-haiku-4-5-20251001",
        "balanced": "claude-sonnet-4-6",
        "powerful": "claude-opus-4-8",
    },
    "deepseek": {
        "fast": "deepseek-chat",
        "balanced": "deepseek-chat",
        "powerful": "deepseek-reasoner",
    },
}
