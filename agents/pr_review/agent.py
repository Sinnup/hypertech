"""
PR Review agent — reviews generated code for style, architecture adherence,
and naming conventions before the security scan. Uses the FAST model tier.

Runs on the feature branch committed by the coder agent.
Returns a structured review report; blocks only on critical issues.
"""

import json
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get, get_optional
from core.tracing.langfuse import get_client, record_generation

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))


def _update_registry(ticket_id: str, updates: dict):
    registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
    if ticket_id in registry:
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _fetch_file_from_github(token: str, repo: str, branch: str, path: str) -> str | None:
    """Fetch a file's content from a GitHub branch. Returns text or None."""
    parts = repo.rstrip("/").rstrip(".git").split("/")
    owner, repo_name = parts[-2], parts[-1]
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=headers, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if isinstance(data, list):
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


_SYSTEM = """You are a senior code reviewer. Review the provided code for:
1. Code style issues (naming, formatting, readability)
2. Architecture adherence (no hardcoded secrets, proper separation)
3. Obvious bugs or logic errors
4. Missing error handling at system boundaries

Return JSON:
{{
  "verdict": "approved|changes_requested|blocked",
  "issues": [{{"severity": "critical|major|minor", "line": 0, "description": "..."}}],
  "summary": "...",
  "approved": true|false
}}

verdict=blocked only if there are critical security issues.
verdict=changes_requested for major issues.
verdict=approved if only minor issues remain.
Return raw JSON only."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "File: {filename}\n\nCode:\n{code}"),
])


@observe(name="pr-review-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_out = state.get("coder_output") or {}
    filename = coder_out.get("filename", "")
    local_path = coder_out.get("local_path", "")

    slack.status(ticket_id, "🔍 PR Review agent started — reviewing generated code...")

    # Try local file first, then GitHub
    code = None
    if local_path and Path(local_path).exists():
        code = Path(local_path).read_text()
    else:
        try:
            token = get("GITHUB_TOKEN")
            repo = get("GITHUB_REPO")
            branch = f"feature/{ticket_id}"
            code = _fetch_file_from_github(token, repo, branch, filename)
        except Exception:
            pass

    if not code:
        slack.status(ticket_id, "⚠️ PR Review: no code file found — skipping review.")
        state["current_agent"] = "pr_review"
        state["next_agent"] = "security"
        state["status"] = "review_skipped"
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        return state

    llm = get_llm(tier=ModelTier.FAST, temperature=0, max_tokens=2048)
    chain = _PROMPT | llm
    result = chain.invoke({"filename": filename, "code": code[:8000]})
    review = parse_json(result.content)
    record_generation(get_model_id(ModelTier.FAST), result, output={"verdict": review.get("verdict")})

    verdict = review.get("verdict", "approved")
    issues = review.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]
    emoji = {"approved": "✅", "changes_requested": "⚠️", "blocked": "❌"}.get(verdict, "❓")

    slack.status(
        ticket_id,
        f"{emoji} PR Review: *{verdict.upper()}* — "
        f"{len(issues)} issue(s), {len(critical)} critical"
    )

    _update_registry(ticket_id, {"status": f"review_{verdict}"})

    state["current_agent"] = "pr_review"
    state["next_agent"] = "security" if verdict != "blocked" else None
    state["status"] = f"review_{verdict}"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("pr_review", "security", "review_done", ticket_id, review)
    )

    return state
