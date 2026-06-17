"""
Shared LLM response parsing utilities.

Replaces the ~8 duplicated markdown-fence-stripping blocks scattered across
agents.  Also handles DeepSeek reasoner ``<think>`` blocks.
"""

import json
import re


def strip_fences(text: str | None) -> str:
    """
    Strip markdown code fences and ``<think>`` blocks from LLM output.

    Handles:
    - `` ```json ... ``` `` and `` ```html ... ``` `` (any language identifier)
    - ``<think>...</think>`` blocks injected by deepseek-reasoner
    - ``None`` input (guard — returns empty string)
    """
    if text is None:
        return ""

    # Strip DeepSeek reasoner think blocks before anything else
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    if text.startswith("```"):
        # Split on the opening fence
        text = text.split("```", 2)[1]
        # Strip the optional language identifier on the first line
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline:]
        # Remove the closing fence
        text = text.rsplit("```", 1)[0].strip()

    return text


def parse_json(text: str | None) -> dict:
    """Strip fences / think blocks then parse as JSON.

    Raises :class:`json.JSONDecodeError` on invalid JSON so callers that
    need graceful fallback (e.g. architect agent) can catch it.
    """
    return json.loads(strip_fences(text))
