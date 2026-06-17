"""
Intent classifier — reads a human prompt and returns scenario + starting agent.
Uses the FAST model tier (cheapest) since this is a simple classification task.
"""

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe
from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.tracing.langfuse import get_client, record_generation

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
            metadata={"model": get_model_id(ModelTier.FAST), "agent": "orchestrator"},
        )

    llm = get_llm(tier=ModelTier.FAST, temperature=0)
    chain = _PROMPT | llm
    result = chain.invoke({"prompt": prompt})
    parsed = parse_json(result.content)

    record_generation(get_model_id(ModelTier.FAST), result, output=parsed)
    return parsed
