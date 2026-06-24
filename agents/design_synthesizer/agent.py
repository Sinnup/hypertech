"""
Design Synthesizer agent — merges BA compliance report, architect HLD, and UX/UI
design brief into a single unified design_synthesis dict consumed by code agents.

Sits between ux_ui and coder agents in the production/internal pipelines so every
code agent receives a coherent, cross-validated brief instead of three separate blobs.
"""

import json
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe
from langsmith import traceable

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

_SYSTEM = """You are a senior product designer and architect. You receive outputs from three
upstream agents (Business Analyst, Architect, UX/UI Designer) and synthesize them into
a single unified design brief that a developer can implement directly.

Produce a JSON object:
{{
  "title": "...",
  "tech_stack": {{"frontend": "...", "backend": "...", "database": "...", "infra": "..."}},
  "screens": [{{"name": "...", "purpose": "...", "key_components": [...]}}],
  "design_summary": "3-5 sentence description of look, feel, and interactions",
  "critical_requirements": ["requirement 1", ...],
  "compliance_highlights": ["EMV: ...", ...],
  "open_questions": ["..."],
  "ready_for_coding": true
}}

Return raw JSON only, no markdown."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "BA/Compliance report:\n{compliance_json}\n\n"
        "Architect HLD:\n{hld_json}\n\n"
        "UX/UI design brief:\n{brief_json}"
    )),
])


@register(
    "design_synthesizer",
    description="Merges BA + Architect + UX/UI outputs into a unified design brief for code agents",
    tier=ModelTier.BALANCED,
    tags=["synthesis", "design"],
)
@observe(name="design-synthesizer-agent")
@traceable(name="design_synthesizer", run_type="chain")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    compliance = state.get("compliance_report") or {}
    hld = state.get("hld_output") or {}
    brief = state.get("design_brief") or {}

    slack.status(ticket_id, "🔀 Design Synthesizer — merging BA + Architect + UX/UI outputs...")

    client = get_client()
    if client:
        client.update_current_span(input={
            "has_compliance": bool(compliance),
            "has_hld": bool(hld),
            "has_brief": bool(brief),
        })

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.1)
    chain = _PROMPT | llm
    result = chain.invoke({
        "compliance_json": json.dumps(compliance, indent=2)[:1500],
        "hld_json": json.dumps(hld, indent=2)[:1500],
        "brief_json": json.dumps(brief, indent=2)[:1000],
    })

    synthesis = parse_json(result.content)
    record_generation(get_model_id(ModelTier.BALANCED), result, output={
        "screens": len(synthesis.get("screens", [])),
        "ready": synthesis.get("ready_for_coding"),
    })

    slack.status(
        ticket_id,
        f"✅ Design synthesis complete — {len(synthesis.get('screens', []))} screen(s), "
        f"ready_for_coding={synthesis.get('ready_for_coding', False)}"
    )

    registry_store.update_ticket(ticket_id, {
        "status": "design_synthesized",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["design_synthesizer"],
    })

    next_agent = _pick_next_agent(state, synthesis)

    state["design_synthesis"] = synthesis
    state["current_agent"] = "design_synthesizer"
    state["next_agent"] = next_agent
    state["status"] = "design_synthesized"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["design_synthesizer"] = AgentOutput(
        status="ok",
        data=synthesis,
        confidence=0.88,
        agent_name="design_synthesizer",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("design_synthesizer", next_agent or "none", "synthesis_ready", ticket_id, {
            "screens": len(synthesis.get("screens", [])),
            "next_agent": next_agent,
        })
    )

    return state


def _pick_next_agent(state: PipelineState, synthesis: dict) -> str | None:
    """Choose next coder agent based on plan or tech stack."""
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    if plan and idx < len(plan):
        return plan[idx]

    # Fallback: infer from tech stack
    tech = synthesis.get("tech_stack", {})
    frontend = tech.get("frontend", "").lower()
    if any(kw in frontend for kw in ("android", "kotlin", "compose", "react native")):
        return "coder_mobile"
    if any(kw in frontend for kw in ("react", "next", "vue", "web")):
        return "coder_web"
    return "coder_backend"
