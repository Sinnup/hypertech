"""
Context Packer agent — compresses agent outputs into bullet-point summaries
for the next agent in the pipeline.

This is a dedicated, visible LangGraph node.  Every agent's output passes
through here before being handed to the next agent.

Design decision (from Q5): visible node in the dashboard — "all steps must be visible."
"""

from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from core.tracing.langfuse import observe

from core.ai import get_llm, get_model_id, ModelTier
from core.state.pipeline_state import PipelineState
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.prompt_registry import get_prompt
from core.tracing.langfuse import record_generation
from core.notifications import slack


_SYSTEM = get_prompt("context_packer", "system")

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Agent: {agent_name}\n\nFull output:\n{output_json}"),
])


@register(
    "context_packer",
    description="Compresses agent outputs into bullet-point summaries for next-agent handoff",
    tier=ModelTier.FAST,
    tags=["infrastructure"],
    visible=True,
)
@observe(name="context-packer")
def run(state: PipelineState) -> PipelineState:
    """Summarize the last agent's output and prepare context for the next agent."""
    current = state["current_agent"]
    ticket_id = state["ticket_id"]

    # Get the output that was just validated
    raw_output = state.get("agent_outputs", {}).get(current, {})
    if not raw_output:
        # Nothing to pack — pass through
        state["current_agent"] = "context_packer"
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        return state

    slack.status(ticket_id, f"📦 Packing context from *{current}* for the next agent…")

    # Call FAST-tier LLM to compress
    import json
    output_str = json.dumps(raw_output, indent=2, default=str)[:6000]

    llm = get_llm(tier=ModelTier.FAST, temperature=0)
    chain = _PROMPT | llm
    result = chain.invoke({
        "agent_name": current,
        "output_json": output_str,
    })

    summary = result.content.strip()
    record_generation(get_model_id(ModelTier.FAST), result, output=summary[:200])

    # Store summary
    state["context_summaries"][current] = summary
    state["current_summary"] = summary

    # Update the AgentOutput in agent_outputs to include the summary
    agent_output = state["agent_outputs"].get(current, {})
    if isinstance(agent_output, dict):
        agent_output["summary"] = summary
        state["agent_outputs"][current] = agent_output

    # Advance routing state
    state["current_agent"] = "context_packer"
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0) + 1
    state["agent_plan_index"] = idx

    # Only update next_agent when following the dynamic plan.
    # When agent_plan is empty (legacy path), preserve whatever next_agent
    # the preceding agent already set — don't overwrite it.
    if plan and idx < len(plan):
        state["next_agent"] = plan[idx]
        slack.status(ticket_id, f"➡️ Handing off to *{plan[idx]}* (step {idx}/{len(plan) - 1})")
    elif plan and idx >= len(plan):
        state["next_agent"] = None

    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    return state
