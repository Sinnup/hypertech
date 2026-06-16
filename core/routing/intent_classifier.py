"""
Intent classifier — reads a human prompt and returns scenario + starting agent.
Uses Claude Haiku (cheapest) since this is a simple classification task.
"""

import json
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe
from core.secrets.loader import get
from core.tracing.langfuse import get_client

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


@observe(name="intent-classifier", as_type="generation")
def classify(prompt: str, ticket_id: str = None) -> dict:
    client = get_client()
    if client:
        client.update_current_generation(
            input={"prompt": prompt},
            metadata={"model": "claude-haiku-4-5-20251001", "agent": "orchestrator"},
        )

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        temperature=0,
    )
    chain = _PROMPT | llm
    result = chain.invoke({"prompt": prompt})
    content = result.content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rsplit("```", 1)[0].strip()

    parsed = json.loads(content)

    usage = result.usage_metadata or {}
    if client:
        client.update_current_generation(
            output=parsed,
            model="claude-haiku-4-5-20251001",
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )
    return parsed
