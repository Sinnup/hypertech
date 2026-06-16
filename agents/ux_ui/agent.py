"""
UX/UI agent — generates a structured design brief as JSON and a lightweight
HTML wireframe for human review.

The design brief is consumed directly by the Coder agent (no Figma dependency
for POC). Figma API upgrade path documented in implementation-plan.md §4.4.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get
from core.tracing.langfuse import get_client

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


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith(("json", "html")):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return text


@observe(name="ux-ui-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    compliance = state.get("compliance_report") or {}

    slack.status(ticket_id, "🎨 UX/UI agent started — generating design brief...")

    requirements = [r["description"] for r in compliance.get("requirements", [])]
    compliance_status = compliance.get("overall_status", "unknown")

    llm_sonnet = ChatAnthropic(
        model="claude-sonnet-4-6",
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        temperature=0.4,
    )

    # Step 1: Generate design brief JSON
    brief_chain = _BRIEF_PROMPT | llm_sonnet
    brief_result = brief_chain.invoke({
        "prompt": prompt,
        "requirements": "\n".join(f"- {r}" for r in requirements) or "None specified",
        "compliance_status": compliance_status,
    })
    brief_json_str = _strip_fences(brief_result.content)
    design_brief = json.loads(brief_json_str)

    slack.status(
        ticket_id,
        f"✅ Design brief ready — {len(design_brief.get('screens', []))} screen(s) defined"
    )

    # Step 2: Generate HTML wireframe
    slack.status(ticket_id, "🖼️ Generating HTML wireframe...")
    wireframe_chain = _WIREFRAME_PROMPT | llm_sonnet
    wireframe_result = wireframe_chain.invoke({"brief_json": brief_json_str})
    wireframe_html = _strip_fences(wireframe_result.content)

    # Save wireframe locally
    wireframe_path = Path(f"poc/{ticket_id}/wireframe.html")
    wireframe_path.parent.mkdir(parents=True, exist_ok=True)
    wireframe_path.write_text(wireframe_html)

    client = get_client()
    if client:
        usage1 = brief_result.usage_metadata or {}
        usage2 = wireframe_result.usage_metadata or {}
        client.update_current_generation(
            model="claude-sonnet-4-6",
            output={"screens": len(design_brief.get("screens", []))},
            usage_details={
                "input": usage1.get("input_tokens", 0) + usage2.get("input_tokens", 0),
                "output": usage1.get("output_tokens", 0) + usage2.get("output_tokens", 0),
            },
        )

    slack.status(ticket_id, f"📐 Wireframe saved at poc/{ticket_id}/wireframe.html")

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
