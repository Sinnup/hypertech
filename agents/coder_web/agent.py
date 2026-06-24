"""
Web Coder agent — generates a complete, self-contained web prototype.

POC: single HTML file with Tailwind CSS (CDN) + vanilla JS.
No build tools, no npm — everything inline or via CDN.
Saves to poc/{ticket_id}/index.html and passes it to the infra agent for serving.
"""

from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe
from langsmith import traceable

from core.ai import get_llm, get_model_id, strip_fences, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

_SYSTEM = """You are an expert web developer. Generate a complete, self-contained web prototype.

Rules:
- Single HTML file with Tailwind CSS from CDN (https://cdn.tailwindcss.com).
- Use vanilla JS only — no npm, no build tools, no React (POC only).
- Populate with realistic dummy data.
- Clean, modern, mobile-responsive layout.
- Include basic interactivity (button clicks, form validation, tab navigation).
- Return ONLY the HTML code, no explanation, no markdown fences."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "Build a web prototype for: {prompt}\n\n"
        "Tech stack: {tech_stack}\n\n"
        "Design summary: {design_summary}\n\n"
        "Screens: {screens}"
    )),
])


@register(
    "coder_web",
    description="Generates single-file HTML+Tailwind+JS web prototype",
    tier=ModelTier.BALANCED,
    tags=["code-generation", "web", "frontend"],
)
@observe(name="coder-web-agent")
@traceable(name="coder_web", run_type="chain")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    design_brief = state.get("design_brief") or {}
    synthesis = state.get("design_synthesis") or {}
    hld = state.get("hld_output") or {}

    slack.status(ticket_id, "🌐 Web Coder started — generating web prototype...")

    screens = [s.get("name", "?") for s in design_brief.get("screens", synthesis.get("screens", []))]
    tech_stack = synthesis.get("tech_stack") or hld.get("tech_stack") or {"frontend": "Vanilla JS + Tailwind"}
    design_summary = synthesis.get("design_summary", "")

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "screens": screens})

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.3)
    chain = _PROMPT | llm
    result = chain.invoke({
        "prompt": prompt,
        "tech_stack": str(tech_stack),
        "design_summary": design_summary or "Clean, modern web UI",
        "screens": ", ".join(screens) or "Main screen",
    })

    html = strip_fences(result.content)
    record_generation(get_model_id(ModelTier.BALANCED), result, output={"html_chars": len(html)})

    # Save to poc/{ticket_id}/index.html
    out_dir = Path(f"poc/{ticket_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(html, encoding="utf-8")

    slack.status(ticket_id, f"✅ Web prototype saved — `poc/{ticket_id}/index.html`")

    registry_store.update_ticket(ticket_id, {
        "status": "code_generated",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["coder_web"],
    })

    coder_output = {
        "project_type": "web",
        "local_path": str(out_file.resolve()),
    }
    state["coder_output"] = coder_output
    state["current_agent"] = "coder_web"
    state["next_agent"] = "infra"
    state["status"] = "code_generated"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["coder_web"] = AgentOutput(
        status="ok",
        data=coder_output,
        confidence=0.85,
        agent_name="coder_web",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("coder_web", "infra", "code_ready", ticket_id, coder_output)
    )

    return state
