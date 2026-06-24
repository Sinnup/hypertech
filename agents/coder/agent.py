"""
Coder agent — selects a template, generates code, commits to a feature branch.
POC scenario: picks MVC template, wires dummy data, commits via GitHub API.
"""

import os
import json
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, strip_fences, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.circuit_breaker import circuit_breaker, CircuitBreakerError
from core.notifications import slack
from core.secrets.loader import get
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store

_SYSTEM = """You are an expert software developer. Given a product description, generate a simple
single-file HTML+CSS+JS prototype that demonstrates the UI flow with dummy data.
The output must be a complete, self-contained HTML file. No external dependencies except
CDN links. Use a clean, modern design with Tailwind CSS from CDN.
Return ONLY the HTML code, no explanation."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Build a POC prototype for: {prompt}"),
])


def _github_fallback(token: str, repo: str, ticket_id: str, filename: str, content: str) -> str:
    """Fallback: return local path when GitHub is unreachable (file already saved)."""
    local_path = Path(filename).resolve()
    slack.alert(
        f"⚠️ GitHub API unavailable — file saved locally: `{local_path}`"
    )
    return str(local_path)


@circuit_breaker(
    service_name="github_api",
    fallback=_github_fallback,
    fallback_label="Save locally — skip GitHub commit",
    timeout=15.0,
)
def _github_commit(token: str, repo: str, ticket_id: str, filename: str, content: str) -> str:
    """Commit a file to a new feature branch via GitHub API. Returns the branch URL."""
    # Parse owner/repo from URL
    parts = repo.rstrip("/").rstrip(".git").split("/")
    owner, repo_name = parts[-2], parts[-1]

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    base = f"https://api.github.com/repos/{owner}/{repo_name}"
    branch = f"feature/{ticket_id}"

    # Get main branch SHA
    r = requests.get(f"{base}/git/ref/heads/main", headers=headers)
    if r.status_code == 404:
        r = requests.get(f"{base}/git/ref/heads/master", headers=headers)
    sha = r.json()["object"]["sha"]

    # Create feature branch
    requests.post(f"{base}/git/refs", headers=headers, json={
        "ref": f"refs/heads/{branch}",
        "sha": sha,
    })

    # Commit the file
    encoded = base64.b64encode(content.encode()).decode()
    requests.put(f"{base}/contents/{filename}", headers=headers, json={
        "message": f"feat({ticket_id}): POC prototype",
        "content": encoded,
        "branch": branch,
    })

    return f"https://github.com/{owner}/{repo_name}/tree/{branch}"


@observe(name="generate-poc")
def _generate_html(prompt: str) -> str:
    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.3)
    chain = _PROMPT | llm
    llm_result = chain.invoke({"prompt": prompt})
    record_generation(get_model_id(ModelTier.BALANCED), llm_result, output=llm_result.content)
    return llm_result.content


@register("coder", description="Generates POC code (HTML/JS), commits to GitHub, saves locally",
          tier=ModelTier.BALANCED, tags=["code-generation"])
@observe(name="coder-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]

    slack.status(ticket_id, "💻 Coder agent started — generating POC prototype...")

    raw = _generate_html(prompt)
    html_code = strip_fences(raw)

    slack.status(ticket_id, "✅ Code generated — committing to GitHub...")

    token = get("GITHUB_TOKEN")
    repo = get("GITHUB_REPO")
    filename = f"poc/{ticket_id}/index.html"

    # Save locally so the infra agent can serve without a GitHub round-trip
    local_path = Path(filename)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(html_code)

    branch_url = _github_commit(token, repo, ticket_id, filename, html_code)

    slack.status(ticket_id, f"📦 Committed to branch: {branch_url}")

    # Write changelog
    changelog_path = Path(f"changelogs/{ticket_id}.md")
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    changelog_path.write_text(
        f"# {ticket_id}\n\n"
        f"**Date:** {datetime.now(timezone.utc).isoformat()}\n"
        f"**Scenario:** POC\n"
        f"**Prompt:** {prompt}\n\n"
        f"## Changes\n- Generated POC prototype (`{filename}`)\n"
        f"- Branch: {branch_url}\n"
    )

    registry_store.update_ticket(ticket_id, {
        "status": "code_committed",
        "agents_involved": ["orchestrator", "coder"],
        "branch": f"feature/{ticket_id}",
    })

    state["current_agent"] = "coder"
    state["next_agent"] = "infra"
    state["status"] = "code_committed"
    state["coder_output"] = {
        "project_type": "web",
        "filename": filename,
        "local_path": str(local_path.resolve()),
        "branch_url": branch_url,
        "lines": len(html_code.splitlines()),
    }
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("coder", "infra", "code_ready", ticket_id, state["coder_output"])
    )

    state["agent_outputs"]["coder"] = AgentOutput(
        status="ok",
        data=state["coder_output"],
        confidence=0.90,
        agent_name="coder",
    ).model_dump()

    return state
