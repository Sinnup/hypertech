"""
Architect agent — produces a High-Level Design (HLD) document as structured JSON.
Uses Opus for deep architectural reasoning.

Receives the compliance report + design brief from prior agents.
Returns HLD that feeds into coder and infra agents for production/internal paths.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get, get_optional
from core.tracing.langfuse import get_client

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))


def _update_registry(ticket_id: str, updates: dict):
    registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
    if ticket_id in registry:
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


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

    llm = ChatAnthropic(
        model="claude-opus-4-8",
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        temperature=0.2,
        max_tokens=4096,
    )
    chain = _PROMPT | llm
    result = chain.invoke({
        "prompt": prompt,
        "scenario": scenario,
        "compliance_json": json.dumps(compliance, indent=2)[:3000],
        "screen_names": ", ".join(screen_names) or "not yet defined",
    })
    content = result.content.strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.rsplit("```", 1)[0].strip()

    hld = json.loads(content)

    usage = result.usage_metadata or {}
    client = get_client()
    if client:
        client.update_current_generation(
            model="claude-opus-4-8",
            output={"components": len(hld.get("components", []))},
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )

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

    _update_registry(ticket_id, {
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
