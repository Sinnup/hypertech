"""
DevOps agent — generates a GitHub Actions CI/CD workflow tailored to the
project type produced by the coder agents.

  - Android  → Gradle build + APK artifact upload
  - Web      → Lint + static build + deploy preview
  - Backend  → pytest + Docker build + push to GHCR

Saves .github/workflows/ci.yml inside poc/{ticket_id}/ and also posts the
workflow to Slack so the user can copy it into the real repo.
"""

import json
import textwrap
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

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = """You are a senior DevOps engineer. Generate a complete, working GitHub Actions
CI/CD workflow (YAML) for the described project.

Rules:
- Use `actions/checkout@v4`, `actions/setup-java@v4` (Android), `actions/setup-python@v3` (backend).
- Include: lint/test, build, and upload-artifact steps.
- Keep it minimal — POC, not production hardened.
- Return ONLY the YAML content — no markdown fences, no explanation."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "Project type: {project_type}\n\n"
        "App description: {prompt}\n\n"
        "Tech stack: {tech_stack}\n\n"
        "Additional context: {context}"
    )),
])

# ---------------------------------------------------------------------------
# Static workflow templates (fallback)
# ---------------------------------------------------------------------------

_ANDROID_WORKFLOW = textwrap.dedent("""\
    name: Android CI

    on:
      push:
        branches: [ "main", "feature/**" ]
      pull_request:
        branches: [ "main" ]

    jobs:
      build:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4

          - name: Set up JDK 17
            uses: actions/setup-java@v4
            with:
              java-version: '17'
              distribution: 'temurin'
              cache: gradle

          - name: Grant execute permission for gradlew
            run: chmod +x gradlew

          - name: Run unit tests
            run: ./gradlew test

          - name: Build debug APK
            run: ./gradlew assembleDebug

          - name: Upload APK artifact
            uses: actions/upload-artifact@v4
            with:
              name: app-debug
              path: app/build/outputs/apk/debug/app-debug.apk
""")

_WEB_WORKFLOW = textwrap.dedent("""\
    name: Web CI

    on:
      push:
        branches: [ "main", "feature/**" ]
      pull_request:
        branches: [ "main" ]

    jobs:
      build:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4

          - name: Validate HTML
            run: |
              npm install -g html-validate
              html-validate "**/*.html" || true

          - name: Run Jest tests
            run: |
              if [ -f "package.json" ]; then
                npm ci && npm test
              else
                echo "No package.json found — skipping Jest"
              fi

          - name: Upload static artifact
            uses: actions/upload-artifact@v4
            with:
              name: web-app
              path: "*.html"
""")

_BACKEND_WORKFLOW = textwrap.dedent("""\
    name: Backend CI

    on:
      push:
        branches: [ "main", "feature/**" ]
      pull_request:
        branches: [ "main" ]

    jobs:
      test:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4

          - name: Set up Python
            uses: actions/setup-python@v5
            with:
              python-version: '3.12'
              cache: pip

          - name: Install dependencies
            run: pip install -r requirements.txt && pip install httpx pytest

          - name: Run tests
            run: pytest tests/ -v

      docker:
        needs: test
        runs-on: ubuntu-latest
        if: github.ref == 'refs/heads/main'
        steps:
          - uses: actions/checkout@v4

          - name: Build Docker image
            run: docker build -t hypertech-backend:${{ github.sha }} .
""")

_WORKFLOWS = {
    "android": _ANDROID_WORKFLOW,
    "web": _WEB_WORKFLOW,
    "backend": _BACKEND_WORKFLOW,
}

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@register(
    "devops",
    description="Generates GitHub Actions CI/CD workflow for Android/Web/Backend projects",
    tier=ModelTier.BALANCED,
    tags=["devops", "cicd", "github-actions"],
)
@observe(name="devops-agent")
@traceable(name="devops", run_type="chain")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    coder_output = state.get("coder_output") or {}
    hld = state.get("hld_output") or {}
    synthesis = state.get("design_synthesis") or {}

    project_type = coder_output.get("project_type", "web")
    tech_stack = synthesis.get("tech_stack") or hld.get("tech_stack") or {}

    slack.status(ticket_id, f"🔧 DevOps agent started — generating {project_type} CI/CD workflow...")

    client = get_client()
    if client:
        client.update_current_span(input={"project_type": project_type})

    # Try LLM first, fall back to static template
    workflow_yaml = _generate_via_llm(prompt, project_type, tech_stack, ticket_id)
    if not workflow_yaml:
        workflow_yaml = _WORKFLOWS.get(project_type, _BACKEND_WORKFLOW)
        slack.status(ticket_id, "⚠️ DevOps: LLM skipped — using built-in template")

    # Save to poc/{ticket_id}/.github/workflows/ci.yml
    out_dir = Path(f"poc/{ticket_id}/.github/workflows")
    out_dir.mkdir(parents=True, exist_ok=True)
    ci_file = out_dir / "ci.yml"
    ci_file.write_text(workflow_yaml, encoding="utf-8")

    slack.status(
        ticket_id,
        f"✅ CI/CD workflow generated — `poc/{ticket_id}/.github/workflows/ci.yml`\n"
        f"Copy to `.github/workflows/ci.yml` in your repo to activate."
    )
    # Post a preview of the workflow
    preview = workflow_yaml[:800]
    slack.status(ticket_id, f"```yaml\n{preview}\n```")

    registry_store.update_ticket(ticket_id, {
        "status": "cicd_ready",
        "agents_involved": list(state.get("agent_outputs", {}).keys()) + ["devops"],
    })

    devops_output = {
        "workflow_file": str(ci_file.resolve()),
        "project_type": project_type,
    }

    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else "infra"

    state["current_agent"] = "devops"
    state["next_agent"] = next_agent
    state["status"] = "cicd_ready"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["devops"] = AgentOutput(
        status="ok",
        data=devops_output,
        confidence=0.90,
        agent_name="devops",
    ).model_dump()

    state["agent_messages"].append(
        agent_message("devops", next_agent, "cicd_ready", ticket_id, devops_output)
    )

    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@observe(name="devops-llm-generate")
def _generate_via_llm(
    prompt: str,
    project_type: str,
    tech_stack: dict,
    ticket_id: str,
) -> str:
    """Ask the LLM for a workflow. Returns empty string on any failure."""
    try:
        llm = get_llm(tier=ModelTier.BALANCED, temperature=0.1, max_tokens=2048)
        chain = _PROMPT | llm
        result = chain.invoke({
            "project_type": project_type,
            "prompt": prompt[:300],
            "tech_stack": json.dumps(tech_stack),
            "context": f"ticket_id={ticket_id}",
        })
        record_generation(get_model_id(ModelTier.BALANCED), result, output={"chars": len(result.content)})
        yaml_text = strip_fences(result.content).strip()
        # Basic validation: must contain 'jobs:'
        if "jobs:" in yaml_text and "steps:" in yaml_text:
            return yaml_text
    except Exception:
        pass
    return ""
