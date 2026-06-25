"""
Architect agent — produces a High-Level Design (HLD) document as structured JSON
plus a Mermaid C4/sequence diagram for Slack visualization and human review.
Uses the POWERFUL model tier for deep architectural reasoning.

Receives the compliance report + design brief from prior agents.
Returns HLD + Mermaid diagram that feed into design_synthesizer and code agents.
"""

import json
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from core.tracing.langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, strip_fences, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store


_SYSTEM = """You are a senior software architect for a Mexican fintech company.
Given requirements, compliance report, and design brief, produce an HLD as JSON:
{{
  "title": "...",
  "tech_stack": {{"frontend": "...", "backend": "...", "database": "...", "infra": "..."}},
  "components": [{{"name": "...", "responsibility": "...", "technology": "..."}}],
  "data_model": [{{"entity": "...", "fields": ["..."], "notes": "..."}}],
  "integrations": [{{"name": "...", "type": "REST|gRPC|event", "description": "..."}}],
  "security_controls": [{{"control": "...", "regulation": "...", "implementation": "..."}}],
  "deployment": {{"strategy": "...", "environment": "local|cert|prod", "notes": "..."}},
  "compliance_summary": "...",
  "open_questions": ["..."],
  "human_approval_required": true
}}

Always set human_approval_required to true for production scenarios.
Return raw JSON only, no markdown."""

_MERMAID_SYSTEM = """You are a software architect. Given an HLD JSON, produce a Mermaid C4 component
diagram that shows the main components and their relationships.

Rules:
- Use C4Context or C4Component syntax (C4 Mermaid).
- If C4 is not ideal for this architecture, use a flowchart or sequenceDiagram instead.
- Keep it concise — max 15 nodes.
- Return ONLY the Mermaid code block (```mermaid ... ```), nothing else."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "Product: {prompt}\n\n"
        "Scenario: {scenario}\n\n"
        "Compliance report:\n{compliance_json}\n\n"
        "Design brief screens: {screen_names}"
    )),
])

_MERMAID_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _MERMAID_SYSTEM),
    ("human", "HLD:\n{hld_json}"),
])


@register("architect", description="Produces High-Level Design (HLD) JSON with tech stack, components, security controls",
          tier=ModelTier.POWERFUL, tags=["architecture", "design"])
@observe(name="architect-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    scenario = state.get("scenario", "production")
    compliance = state.get("compliance_report") or {}
    design_brief = state.get("design_brief") or {}

    slack.status(ticket_id, "🏛️ Architect agent started — designing HLD...")

    screen_names = [s.get("name", "?") for s in design_brief.get("screens", [])]

    llm = get_llm(tier=ModelTier.POWERFUL, max_tokens=8192)
    chain = _PROMPT | llm
    result = chain.invoke({
        "prompt": prompt,
        "scenario": scenario,
        "compliance_json": json.dumps(compliance, indent=2)[:2000],
        "screen_names": ", ".join(screen_names) or "not yet defined",
    })

    try:
        hld = parse_json(result.content)
    except json.JSONDecodeError:
        # Truncated output — build a minimal HLD so the pipeline continues
        hld = {
            "title": f"HLD for {prompt[:60]}",
            "tech_stack": {"frontend": "React Native", "backend": "Python/FastAPI", "database": "PostgreSQL", "infra": "Docker/ECS"},
            "components": [{"name": "Core", "responsibility": "Main app logic", "technology": "Python"}],
            "data_model": [],
            "integrations": [],
            "security_controls": [],
            "deployment": {"strategy": "containerized", "environment": "local", "notes": ""},
            "compliance_summary": "See compliance report",
            "open_questions": ["Full HLD generation failed — increase max_tokens or simplify prompt"],
            "human_approval_required": True,
            "_parse_error": True,
        }
        slack.status(ticket_id, "⚠️ Architect: HLD JSON truncated — using minimal fallback. Human review required.")

    record_generation(get_model_id(ModelTier.POWERFUL), result, output={"components": len(hld.get("components", []))})

    # Generate Mermaid diagram using BALANCED tier (cheaper than POWERFUL)
    mermaid_diagram = _generate_mermaid(hld, ticket_id)
    hld["mermaid_diagram"] = mermaid_diagram

    slack.status(
        ticket_id,
        f"✅ HLD ready — {len(hld.get('components', []))} component(s), "
        f"stack: {hld.get('tech_stack', {}).get('backend', '?')} / "
        f"{hld.get('tech_stack', {}).get('frontend', '?')}"
    )

    if mermaid_diagram:
        slack.status(ticket_id, f"📐 Architecture diagram:\n```\n{mermaid_diagram[:800]}\n```")

    if hld.get("human_approval_required"):
        summary = (
            f"*Stack:* {hld.get('tech_stack', {})}\n"
            f"*Components:* {len(hld.get('components', []))}\n"
            f"*Compliance:* {hld.get('compliance_summary', 'see report')}"
        )
        slack.approval_request(ticket_id, stage="hld_review", summary=summary)

    registry_store.update_ticket(ticket_id, {
        "status": "hld_ready",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["architect"],
    })

    # Determine next agent from plan or legacy routing
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else "design_synthesizer"

    state["hld_output"] = hld
    state["current_agent"] = "architect"
    state["next_agent"] = next_agent
    state["status"] = "hld_ready"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["architect"] = AgentOutput(
        status="ok",
        data={"components": len(hld.get("components", [])), "has_diagram": bool(mermaid_diagram)},
        confidence=0.90,
        agent_name="architect",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("architect", next_agent, "hld_ready", ticket_id, {
            "components": len(hld.get("components", [])),
            "has_mermaid": bool(mermaid_diagram),
        })
    )

    return state


@observe(name="architect-mermaid")
def _generate_mermaid(hld: dict, ticket_id: str) -> str:
    """Generate a Mermaid diagram for the HLD. Returns empty string on failure."""
    try:
        llm = get_llm(tier=ModelTier.BALANCED, temperature=0.1, max_tokens=1024)
        chain = _MERMAID_PROMPT | llm
        result = chain.invoke({"hld_json": json.dumps(hld, indent=2)[:2000]})
        diagram = strip_fences(result.content).strip()
        if diagram.startswith("```"):
            diagram = diagram[3:].strip()
        if diagram.endswith("```"):
            diagram = diagram[:-3].strip()
        return diagram
    except Exception as exc:
        slack.status(ticket_id, f"⚠️ Mermaid diagram generation skipped: {exc}")
        return ""
