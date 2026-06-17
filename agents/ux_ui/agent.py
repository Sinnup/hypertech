"""
UX/UI agent — generates a structured design brief as JSON and a lightweight
HTML wireframe for human review.

The design brief is consumed directly by the Coder agent (no Figma dependency
for POC). Figma API upgrade path documented in implementation-plan.md §4.4.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, strip_fences, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation, sum_token_usage
import core.registry as registry_store

_BRIEF_SYSTEM = """You are a senior UX/UI designer for a Mexican fintech app.
Given a product description and compliance requirements, produce a design brief as JSON:
{{
  "screens": [
    {{
      "name": "ScreenName",
      "purpose": "...",
      "components": ["ComponentA", "ComponentB"],
      "interactions": ["tap X → navigate to Y"],
      "data_fields": ["field1", "field2"]
    }}
  ],
  "color_tokens": {{"primary": "#hex", "secondary": "#hex", "background": "#hex", "error": "#hex"}},
  "typography": {{"heading": "font/size", "body": "font/size", "caption": "font/size"}},
  "design_principles": ["principle1", "principle2"],
  "accessibility_notes": "..."
}}

Keep it implementation-focused — the Coder agent will use this JSON directly.
Return raw JSON only, no markdown."""

_WIREFRAME_SYSTEM = """You are a senior UX/UI designer. Given a design brief JSON, generate a
single self-contained HTML file that renders a lightweight wireframe of all screens.
Use Tailwind CSS from CDN. Show each screen as a mobile phone mockup (390px wide).
Include basic navigation between screens using tab clicks.
Return ONLY the HTML code, no explanation."""

_BRIEF_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _BRIEF_SYSTEM),
    ("human", "Product: {prompt}\n\nRequirements summary: {requirements}\nCompliance status: {compliance_status}"),
])

_WIREFRAME_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _WIREFRAME_SYSTEM),
    ("human", "Design brief:\n{brief_json}"),
])


@observe(name="ux-ui-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    compliance = state.get("compliance_report") or {}

    slack.status(ticket_id, "🎨 UX/UI agent started — generating design brief...")

    requirements = [r["description"] for r in compliance.get("requirements", [])]
    compliance_status = compliance.get("overall_status", "unknown")

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.4)

    # Step 1: Generate design brief JSON
    brief_chain = _BRIEF_PROMPT | llm
    brief_result = brief_chain.invoke({
        "prompt": prompt,
        "requirements": "\n".join(f"- {r}" for r in requirements) or "None specified",
        "compliance_status": compliance_status,
    })
    design_brief = parse_json(brief_result.content)

    slack.status(
        ticket_id,
        f"✅ Design brief ready — {len(design_brief.get('screens', []))} screen(s) defined"
    )

    # Step 2: Generate HTML wireframe
    slack.status(ticket_id, "🖼️ Generating HTML wireframe...")
    wireframe_chain = _WIREFRAME_PROMPT | llm
    wireframe_result = wireframe_chain.invoke({"brief_json": json.dumps(design_brief)})
    wireframe_html = strip_fences(wireframe_result.content)

    # Save wireframe locally
    wireframe_path = Path(f"poc/{ticket_id}/wireframe.html")
    wireframe_path.parent.mkdir(parents=True, exist_ok=True)
    wireframe_path.write_text(wireframe_html)

    # Record combined token usage from both LLM calls
    usage = sum_token_usage(brief_result, wireframe_result)
    client = get_client()
    if client:
        client.update_current_generation(
            model=get_model_id(ModelTier.BALANCED),
            output={"screens": len(design_brief.get("screens", []))},
            usage_details=usage,
        )

    slack.status(ticket_id, f"📐 Wireframe saved at poc/{ticket_id}/wireframe.html")

    registry_store.update_ticket(ticket_id, {
        "status": "design_ready",
        "agents_involved": ["orchestrator", "ba_compliance", "ux_ui"],
    })

    state["design_brief"] = design_brief
    state["current_agent"] = "ux_ui"
    state["next_agent"] = None
    state["status"] = "design_ready"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message(
            "ux_ui", "human", "design_ready", ticket_id,
            {"screens": len(design_brief.get("screens", [])), "wireframe_path": str(wireframe_path)}
        )
    )

    return state
