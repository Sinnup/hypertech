"""
Test Generator agent — produces unit/integration tests for whatever coder agent
just wrote.

  - Android (coder_mobile) → Kotlin JUnit4 + Espresso UI test
  - Web     (coder_web)    → Jest + @testing-library/dom test file
  - Backend (coder_backend)→ pytest test module

Saves to poc/{ticket_id}/tests/. Does NOT run the tests (no build toolchain
guaranteed in this environment) — posts instructions to Slack instead.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, strip_fences, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_ANDROID_SYSTEM = """You are an expert Android test engineer using Kotlin.
Given the main Activity source, generate a single Kotlin test file with:
1. At minimum 3 JUnit4 unit tests for business logic (PaymentState transitions, etc.)
2. At least 1 Espresso UI test verifying the main button interaction.

Include all necessary imports. Use `@RunWith(AndroidJUnit4::class)`.
Return ONLY the Kotlin code — no explanation."""

_WEB_SYSTEM = """You are an expert frontend test engineer.
Given an HTML+JS prototype, generate a Jest test file that:
1. Tests at least 3 user interactions (button clicks, form input, navigation).
2. Uses @testing-library/dom for DOM queries.
3. Mocks fetch/API calls where needed.
Return ONLY the JavaScript test code — no explanation."""

_BACKEND_SYSTEM = """You are an expert backend test engineer using Python/pytest.
Given a FastAPI app, generate a pytest test module that:
1. Tests all endpoints (GET, POST, etc.) using TestClient from fastapi.testclient.
2. Tests happy path and at least one error case per endpoint.
3. Includes fixtures where useful.
Return ONLY the Python test code — no explanation."""

_ANDROID_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _ANDROID_SYSTEM),
    ("human", "App description: {prompt}\n\nMainActivity.kt:\n{source_code}"),
])

_WEB_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _WEB_SYSTEM),
    ("human", "App description: {prompt}\n\nindex.html (first 5000 chars):\n{source_code}"),
])

_BACKEND_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _BACKEND_SYSTEM),
    ("human", "App description: {prompt}\n\nmain.py:\n{source_code}"),
])

_PROMPTS = {
    "android": _ANDROID_PROMPT,
    "web": _WEB_PROMPT,
    "backend": _BACKEND_PROMPT,
}

_EXTENSIONS = {
    "android": ("MainActivityTest.kt", "kotlin"),
    "web": ("app.test.js", "javascript"),
    "backend": ("test_main.py", "python"),
}

_RUN_HINTS = {
    "android": "In Android Studio: right-click test file → Run 'MainActivityTest'",
    "web": "npm install --save-dev jest @testing-library/dom && npx jest",
    "backend": "pip install httpx pytest && pytest tests/",
}

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@register(
    "test_generator",
    description="Generates unit + UI tests for coder output (Android/Web/Backend)",
    tier=ModelTier.BALANCED,
    tags=["testing", "quality"],
)
@observe(name="test-generator-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    coder_output = state.get("coder_output") or {}
    project_type = coder_output.get("project_type", "web")

    slack.status(ticket_id, f"🧪 Test Generator started — creating {project_type} tests...")

    source_code = _load_source(coder_output, project_type)

    if not source_code:
        slack.status(ticket_id, "⚠️ Test Generator: no source code found — skipping.")
        return _skip(state, ticket_id)

    # Pick the right prompt
    prompt_template = _PROMPTS.get(project_type, _BACKEND_PROMPT)
    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.2)
    chain = prompt_template | llm

    result = chain.invoke({"prompt": prompt, "source_code": source_code[:6000]})
    test_code = strip_fences(result.content)
    record_generation(get_model_id(ModelTier.BALANCED), result, output={"chars": len(test_code)})

    # Save test file
    filename, lang = _EXTENSIONS.get(project_type, ("test_main.py", "python"))
    test_dir = Path(f"poc/{ticket_id}/tests")
    test_dir.mkdir(parents=True, exist_ok=True)
    test_file = test_dir / filename
    test_file.write_text(test_code, encoding="utf-8")

    run_hint = _RUN_HINTS.get(project_type, "Run tests with your project's test runner")
    slack.status(
        ticket_id,
        f"✅ Tests generated — `poc/{ticket_id}/tests/{filename}`\n"
        f"▶️ *Run:* {run_hint}"
    )

    registry_store.update_ticket(ticket_id, {
        "status": "tests_generated",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["test_generator"],
    })

    test_output = {
        "test_file": str(test_file.resolve()),
        "project_type": project_type,
        "run_hint": run_hint,
    }

    # Determine next agent from plan
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else "infra"

    state["current_agent"] = "test_generator"
    state["next_agent"] = next_agent
    state["status"] = "tests_generated"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["test_generator"] = AgentOutput(
        status="ok",
        data=test_output,
        confidence=0.88,
        agent_name="test_generator",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("test_generator", next_agent, "tests_ready", ticket_id, test_output)
    )

    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_source(coder_output: dict, project_type: str) -> Optional[str]:
    """Load relevant source file(s) for test generation."""
    project_dir = coder_output.get("project_dir", "")
    local_path = coder_output.get("local_path", "")

    if project_type == "android" and project_dir:
        # Look for MainActivity.kt
        candidates = list(Path(project_dir).rglob("MainActivity.kt"))
        if candidates:
            return candidates[0].read_text(encoding="utf-8", errors="replace")

    if project_type == "web" and local_path and Path(local_path).exists():
        return Path(local_path).read_text(encoding="utf-8", errors="replace")

    if project_type == "backend" and project_dir:
        main_py = Path(project_dir) / "main.py"
        if main_py.exists():
            return main_py.read_text(encoding="utf-8", errors="replace")

    return None


def _skip(state: PipelineState, ticket_id: str) -> PipelineState:
    state["current_agent"] = "test_generator"
    state["status"] = "tests_skipped"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_outputs"]["test_generator"] = AgentOutput(
        status="degraded",
        data={"reason": "no source code found"},
        confidence=0.70,
        agent_name="test_generator",
    ).model_dump()
    return state
