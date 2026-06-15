"""
Intent classifier — reads a human prompt and returns scenario + starting agent.
Uses Claude Haiku (cheapest) since this is a simple classification task.
"""

import json
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from core.secrets.loader import get

_SYSTEM = """You are the intent classifier for an agentic software development system.
Given a human prompt, return a JSON object with exactly these fields:
- scenario: one of "poc", "internal", "production"
- starting_agent: one of "coder", "ba_compliance", "ux_ui"
- confidence: float 0-1
- reason: one sentence

Rules:
- poc: quick demo, proof of concept, prototype, "show me something fast", "simple screen"
- internal: internal tool, for the team, admin panel, not client-facing
- production: client-facing, must meet compliance, fintech, payment, EMV, real users

For poc → starting_agent is always "coder"
For internal/production → starting_agent is "ba_compliance"

Respond with raw JSON only, no markdown."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "{prompt}"),
])


def classify(prompt: str) -> dict:
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        temperature=0,
    )
    chain = _PROMPT | llm
    result = chain.invoke({"prompt": prompt})
    return json.loads(result.content)
