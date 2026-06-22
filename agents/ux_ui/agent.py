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
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.integrations.figma import get_figma_client
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


@register("ux_ui", description="Generates design brief JSON + HTML wireframe from requirements",
          tier=ModelTier.BALANCED, tags=["design", "wireframe"])
@observe(name="ux-ui-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    compliance = state.get("compliance_report") or {}

    slack.status(ticket_id, "🎨 UX/UI agent started — generating design brief...")

    requirements = [r["description"] for r in compliance.get("requirements", [])]
    compliance_status = compliance.get("overall_status", "unknown")

    # ── Figma integration: pull design assets & tokens ──────────────────
    figma_context = ""
    figma_file_name = ""
    figma_assets_dir = Path(f"poc/{ticket_id}/assets")
    figma = get_figma_client()

    if figma:
        slack.status(ticket_id, "🎨 Fetching design assets from Figma...")
        try:
            files = figma.get_project_files()
            if files:
                slack.status(
                    ticket_id,
                    f"📐 Figma project has {len(files)} file(s) — "
                    f"extracting tokens and exporting assets..."
                )
                main_file = files[0]
                file_key = main_file["key"]
                figma_file_name = main_file.get("name", file_key)

                # Extract design tokens
                tokens = figma.extract_tokens(file_key)
                token_colors = tokens.get("colors", {})
                token_typo = tokens.get("typography", {})

                # Find top-level frames (screens) and export
                file_data = figma.get_file(file_key, depth=1)
                doc = file_data.get("document", {})
                frames = [
                    c for c in doc.get("children", [])
                    if c.get("type") == "FRAME"
                ]
                if frames:
                    frame_ids = [f["id"] for f in frames[:5]]
                    exported = figma.export_screens(
                        file_key, frame_ids, figma_assets_dir,
                    )
                    slack.status(
                        ticket_id,
                        f"🖼️ Exported {len(exported)} screen(s) from Figma "
                        f"to poc/{ticket_id}/assets/"
                    )
                else:
                    figma.export_screens(
                        file_key, [doc["id"]], figma_assets_dir,
                    )

                # Build context string for the LLM
                color_list = "\n".join(
                    f"  - {name}: {hex_val}"
                    for name, hex_val in list(token_colors.items())[:20]
                )
                typo_list = "\n".join(
                    f"  - {name}: {t.get('family', '?')} {t.get('size', '?')}px @{t.get('weight', '?')}"
                    for name, t in list(token_typo.items())[:10]
                )
                figma_context = (
                    f"\n\nFigma file: {figma_file_name}\n"
                    f"Colors from design system:\n{color_list}\n\n"
                    f"Typography:\n{typo_list}\n\n"
                    f"Use these exact design tokens in the design brief. "
                    f"The wireframe should reflect the Figma design system."
                )
        except Exception as exc:
            slack.status(
                ticket_id,
                f"⚠️ Figma fetch failed ({exc}) — falling back to LLM-only design."
            )

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.4)

    # Step 1: Generate design brief JSON (enriched with Figma data if available)
    brief_chain = _BRIEF_PROMPT | llm
    brief_result = brief_chain.invoke({
        "prompt": prompt + figma_context,
        "requirements": "\n".join(f"- {r}" for r in requirements) or "None specified",
        "compliance_status": compliance_status,
    })
    design_brief = parse_json(brief_result.content)

    # Merge Figma metadata into the design brief
    if figma and figma_context:
        design_brief["figma_file"] = figma_file_name
        design_brief["assets_path"] = str(figma_assets_dir.resolve())

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
    scenario = state.get("scenario", "internal")
    state["current_agent"] = "ux_ui"
    state["next_agent"] = "architect" if scenario == "production" else None
    state["status"] = "design_ready"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message(
            "ux_ui", "human", "design_ready", ticket_id,
            {"screens": len(design_brief.get("screens", [])), "wireframe_path": str(wireframe_path)}
        )
    )

    # Store structured output so context_packer can find it and advance the plan index
    state["agent_outputs"]["ux_ui"] = AgentOutput(
        status="ok",
        data=design_brief,
        confidence=0.92,
        agent_name="ux_ui",
    ).model_dump()

    return state
