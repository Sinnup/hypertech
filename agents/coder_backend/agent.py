"""
Backend Coder agent — generates a minimal, runnable FastAPI/Python backend.

Produces a small set of files: main.py, models.py, requirements.txt, and
optionally a Dockerfile. Saves to poc/{ticket_id}/backend/.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from core.tracing.langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, strip_fences, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

_SYSTEM = """You are an expert backend developer specializing in Python/FastAPI.
Given a product description and requirements, generate a complete, minimal, runnable FastAPI application.

Rules:
- Use FastAPI + Pydantic v2.
- Include all endpoint handlers (no stubs).
- Use in-memory storage (list/dict) unless a real DB is explicitly required.
- Return a JSON object where keys are file paths and values are file contents.
- Include at least: main.py, models.py, requirements.txt.
- Return raw JSON only, no markdown fences."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "Product: {prompt}\n\n"
        "Tech stack: {tech_stack}\n\n"
        "Endpoints needed (from HLD integrations): {integrations}\n\n"
        "Data model: {data_model}"
    )),
])


@register(
    "coder_backend",
    description="Generates FastAPI/Python backend project files for POC",
    tier=ModelTier.BALANCED,
    tags=["code-generation", "backend", "api"],
)
@observe(name="coder-backend-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    hld = state.get("hld_output") or {}
    synthesis = state.get("design_synthesis") or {}

    slack.status(ticket_id, "⚙️ Backend Coder started — generating FastAPI project...")

    tech_stack = synthesis.get("tech_stack") or hld.get("tech_stack") or {"backend": "FastAPI/Python"}
    integrations = hld.get("integrations", [])
    data_model = hld.get("data_model", [])

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "endpoints": len(integrations)})

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.2)
    chain = _PROMPT | llm
    result = chain.invoke({
        "prompt": prompt,
        "tech_stack": json.dumps(tech_stack),
        "integrations": json.dumps(integrations[:5]),
        "data_model": json.dumps(data_model[:5]),
    })

    record_generation(get_model_id(ModelTier.BALANCED), result, output={"chars": len(result.content)})

    file_map = _parse_file_map(result.content)
    if not file_map:
        slack.status(ticket_id, "⚠️ Backend Coder: LLM parse failed — using minimal FastAPI stub")
        file_map = _minimal_fastapi_stub(prompt)

    out_dir = Path(f"poc/{ticket_id}/backend")
    out_dir.mkdir(parents=True, exist_ok=True)
    files_written = []

    for rel_path, content in file_map.items():
        target = out_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files_written.append(str(target))

    slack.status(
        ticket_id,
        f"✅ Backend project ready — {len(files_written)} files in `poc/{ticket_id}/backend/`\n"
        f"Run with: `cd poc/{ticket_id}/backend && uvicorn main:app --reload`"
    )

    registry_store.update_ticket(ticket_id, {
        "status": "code_generated",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["coder_backend"],
    })

    coder_output = {
        "project_type": "backend",
        "project_dir": str(out_dir.resolve()),
        "files": files_written,
        "run_cmd": f"cd {out_dir.resolve()} && uvicorn main:app --reload",
    }
    state["coder_output"] = coder_output
    state["current_agent"] = "coder_backend"
    state["next_agent"] = "infra"
    state["status"] = "code_generated"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["coder_backend"] = AgentOutput(
        status="ok",
        data=coder_output,
        confidence=0.83,
        agent_name="coder_backend",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("coder_backend", "infra", "code_ready", ticket_id, coder_output)
    )

    return state


def _parse_file_map(content: str) -> Optional[dict]:
    cleaned = strip_fences(content).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _minimal_fastapi_stub(prompt: str) -> dict[str, str]:
    return {
        "main.py": f"""\
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from models import Item

app = FastAPI(title="HyperTech POC", description="{prompt[:80]}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_items: list[Item] = []


@app.get("/health")
def health():
    return {{"status": "ok"}}


@app.get("/items")
def list_items():
    return _items


@app.post("/items", status_code=201)
def create_item(item: Item):
    _items.append(item)
    return item
""",
        "models.py": """\
from pydantic import BaseModel
from typing import Optional


class Item(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
""",
        "requirements.txt": "fastapi>=0.110.0\nuvicorn[standard]>=0.27.0\npydantic>=2.0.0\n",
    }
