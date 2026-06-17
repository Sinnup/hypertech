"""
Architect agent — produces a High-Level Design (HLD) document as structured JSON.
Uses the POWERFUL model tier for deep architectural reasoning.

Receives the compliance report + design brief from prior agents.
Returns HLD that feeds into coder and infra agents for production/internal paths.
"""

import json
from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
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

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "Product: {prompt}\n\n"
        "Scenario: {scenario}\n\n"
        "Compliance report:\n{compliance_json}\n\n"
        "Design brief screens: {screen_names}"
    )),
])


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

    slack.status(
        ticket_id,
        f"✅ HLD ready — {len(hld.get('components', []))} component(s), "
        f"stack: {hld.get('tech_stack', {}).get('backend', '?')} / "
        f"{hld.get('tech_stack', {}).get('frontend', '?')}"
    )

    if hld.get("human_approval_required"):
        summary = (
            f"*Stack:* {hld.get('tech_stack', {})}\n"
            f"*Components:* {len(hld.get('components', []))}\n"
            f"*Compliance:* {hld.get('compliance_summary', 'see report')}"
        )
        slack.approval_request(ticket_id, stage="hld_review", summary=summary)

    registry_store.update_ticket(ticket_id, {
        "status": "hld_ready",
        "agents_involved": ["orchestrator", "ba_compliance", "ux_ui", "architect"],
    })

    state["hld_output"] = hld
    state["current_agent"] = "architect"
    state["next_agent"] = "pr_review"
    state["status"] = "hld_ready"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("architect", "pr_review", "hld_ready", ticket_id, {
            "components": len(hld.get("components", [])),
        })
    )

    return state
