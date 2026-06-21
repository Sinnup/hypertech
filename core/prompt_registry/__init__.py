"""
Prompt Registry — public API.

    get_prompt(agent, prompt_type) → str
    list_prompts(agent)            → list[str]
    list_agents_with_prompts()     → list[str]
"""

from core.prompt_registry.loader import (
    get_prompt,
    list_prompts,
    list_agents_with_prompts,
)
