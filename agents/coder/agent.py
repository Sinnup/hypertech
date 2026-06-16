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
from core.secrets.loader import get_optional

REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))

def _update_registry(ticket_id: str, updates: dict):
    registry = json.loads(REGISTRY_PATH.read_text()) if REGISTRY_PATH.exists() else {}
    if ticket_id in registry:
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        REGISTRY_PATH.write_text(json.dumps(registry, indent=2))

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get
from core.tracing.langfuse import get_client

_SYSTEM = """You are an expert software developer. Given a product description, generate a simple
single-file HTML+CSS+JS prototype that demonstrates the UI flow with dummy data.
The output must be a complete, self-contained HTML file. No external dependencies except
CDN links. Use a clean, modern design with Tailwind CSS from CDN.
Return ONLY the HTML code, no explanation."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Build a POC prototype for: {prompt}"),
])


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


@observe(name="generate-poc", as_type="generation")
def _generate_html(prompt: str) -> str:
    llm = ChatAnthropic(
        model="claude-sonnet-4-6",
        anthropic_api_key=get("ANTHROPIC_API_KEY"),
        temperature=0.3,
    )
    chain = _PROMPT | llm
    llm_result = chain.invoke({"prompt": prompt})
    usage = llm_result.usage_metadata or {}
    client = get_client()
    if client:
        client.update_current_generation(
            model="claude-sonnet-4-6",
            input={"prompt": prompt},
            output=llm_result.content,
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )
    return llm_result.content


@observe(name="coder-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]

    slack.status(ticket_id, "💻 Coder agent started — generating POC prototype...")

    raw = _generate_html(prompt)
    raw = raw.strip()
    # Strip markdown code fences if LLM wrapped the output
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("html"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    html_code = raw

    slack.status(ticket_id, "✅ Code generated — committing to GitHub...")

    token = get("GITHUB_TOKEN")
    repo = get("GITHUB_REPO")
    filename = f"poc/{ticket_id}/index.html"

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

    _update_registry(ticket_id, {
        "status": "code_committed",
        "agents_involved": ["orchestrator", "coder"],
        "branch": f"feature/{ticket_id}",
    })

    state["current_agent"] = "coder"
    state["next_agent"] = "infra"
    state["status"] = "code_committed"
    state["coder_output"] = {
        "filename": filename,
        "branch_url": branch_url,
        "lines": len(html_code.splitlines()),
    }
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("coder", "infra", "code_ready", ticket_id, state["coder_output"])
    )

    return state
