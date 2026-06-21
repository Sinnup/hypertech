"""
PR Review agent — reviews generated code for style, architecture adherence,
naming conventions, and obvious bugs before the security scan.

Phase 3 enhancement: iteration loop (max 3 rounds).
  - approved          → advance to next agent in plan (security)
  - changes_requested → inject [coder_agent, pr_review] back into agent_plan
  - blocked           → trigger human_escalation via low-confidence AgentOutput
  - 3rd iteration     → escalate to human regardless of verdict
"""

import json
import base64
import requests
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get, get_optional
from core.tracing.langfuse import get_client, record_generation

_MAX_REVIEW_ITERATIONS = int(get_optional("MAX_REVIEW_ITERATIONS", "3"))

_SYSTEM = """You are a senior code reviewer for a fintech company.
Review the code for:
1. Code style (naming, formatting, readability)
2. Architecture adherence (no hardcoded secrets, proper separation of concerns)
3. Obvious bugs or logic errors
4. Missing error handling at system boundaries
5. Performance red flags

Return JSON:
{{
  "verdict": "approved|changes_requested|blocked",
  "issues": [{{"severity": "critical|major|minor", "line": 0, "description": "...", "fix_hint": "..."}}],
  "summary": "...",
  "approved": true|false
}}

verdict=blocked only for critical security issues that must not go further.
verdict=changes_requested for major issues that a coder can fix.
verdict=approved when only minor issues remain (or none).
Return raw JSON only."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", (
        "File: {filename}\n\n"
        "Review iteration: {iteration} of {max_iterations}\n\n"
        "Prior review issues to check were fixed:\n{prior_issues}\n\n"
        "Code:\n{code}"
    )),
])


@register(
    "pr_review",
    description="Reviews code for style/architecture/bugs; loops back to coder up to 3× on changes_requested",
    tier=ModelTier.FAST,
    tags=["review", "quality"],
)
@observe(name="pr-review-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_out = state.get("coder_output") or {}
    iteration = state.get("iteration_count", 0) + 1

    slack.status(ticket_id, f"🔍 PR Review — iteration {iteration}/{_MAX_REVIEW_ITERATIONS}...")

    # Load code
    code, filename = _load_code(coder_out)
    if not code:
        slack.status(ticket_id, "⚠️ PR Review: no code found — skipping.")
        return _advance(state, ticket_id, "approved", [], iteration)

    # Collect prior issues from previous review iteration (if any)
    prior_review = state.get("agent_outputs", {}).get("pr_review") or {}
    prior_issues = prior_review.get("data", {}).get("issues", [])
    prior_text = "\n".join(
        f"- [{i.get('severity')}] {i.get('description')}" for i in prior_issues
    ) or "None (first review)"

    llm = get_llm(tier=ModelTier.FAST, temperature=0, max_tokens=2048)
    chain = _PROMPT | llm
    result = chain.invoke({
        "filename": filename,
        "iteration": iteration,
        "max_iterations": _MAX_REVIEW_ITERATIONS,
        "prior_issues": prior_text,
        "code": code[:8000],
    })
    review = parse_json(result.content)
    record_generation(get_model_id(ModelTier.FAST), result, output={"verdict": review.get("verdict")})

    verdict = review.get("verdict", "approved")
    issues = review.get("issues", [])
    critical = [i for i in issues if i.get("severity") == "critical"]
    major = [i for i in issues if i.get("severity") == "major"]
    emoji = {"approved": "✅", "changes_requested": "⚠️", "blocked": "❌"}.get(verdict, "❓")

    slack.status(
        ticket_id,
        f"{emoji} PR Review iteration {iteration}: *{verdict.upper()}* — "
        f"{len(issues)} issue(s) ({len(critical)} critical, {len(major)} major)"
    )

    return _advance(state, ticket_id, verdict, issues, iteration, review)


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

def _advance(
    state: PipelineState,
    ticket_id: str,
    verdict: str,
    issues: list,
    iteration: int,
    review: dict | None = None,
) -> PipelineState:
    """Determine routing based on verdict and iteration count."""

    state["iteration_count"] = iteration

    # Save review output
    state["agent_outputs"]["pr_review"] = AgentOutput(
        status="ok" if verdict == "approved" else "degraded",
        data={"verdict": verdict, "issues": issues, "iteration": iteration},
        confidence=_confidence(verdict, iteration),
        agent_name="pr_review",
    ).model_dump()

    state["current_agent"] = "pr_review"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    # ── blocked or max iterations → escalate to human ──────────────────────
    if verdict == "blocked" or (verdict == "changes_requested" and iteration >= _MAX_REVIEW_ITERATIONS):
        reason = (
            f"PR Review blocked after {iteration} iteration(s): "
            f"{review.get('summary', 'critical issues found')}" if review
            else "Max review iterations reached"
        )
        slack.status(ticket_id, f"🚨 PR Review: human escalation — {reason}")
        state["human_escalation"] = True
        state["human_escalation_reason"] = reason
        # Emit confidence below threshold → validation_gate routes to human_escalation
        state["agent_outputs"]["pr_review"]["confidence"] = 0.3
        state["status"] = "review_blocked"
        state["agent_messages"].append(
            agent_message("pr_review", "human_escalation", "review_blocked", ticket_id, {"reason": reason})
        )
        return state

    # ── changes_requested + iterations remaining → loop back to coder ──────
    if verdict == "changes_requested" and iteration < _MAX_REVIEW_ITERATIONS:
        coder_agent = _detect_coder_agent(state)
        slack.status(
            ticket_id,
            f"🔄 PR Review: changes requested — routing back to *{coder_agent}* "
            f"(iteration {iteration}/{_MAX_REVIEW_ITERATIONS})"
        )
        if issues:
            issue_list = "\n".join(f"• [{i.get('severity')}] {i.get('description')} — {i.get('fix_hint','')}" for i in issues[:5])
            slack.status(ticket_id, f"Issues to fix:\n{issue_list}")

        _inject_review_loop(state, coder_agent)
        state["status"] = f"review_changes_requested_iter{iteration}"
        state["agent_messages"].append(
            agent_message("pr_review", coder_agent, "changes_requested", ticket_id, {
                "iteration": iteration,
                "issues": issues,
            })
        )
        return state

    # ── approved → advance to next in plan ─────────────────────────────────
    slack.status(ticket_id, "✅ PR Review approved — proceeding to security scan.")
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else "security"
    state["next_agent"] = next_agent
    state["status"] = "review_approved"
    state["agent_messages"].append(
        agent_message("pr_review", next_agent, "review_approved", ticket_id, {"verdict": "approved"})
    )
    return state


def _inject_review_loop(state: PipelineState, coder_agent: str) -> None:
    """Splice [coder_agent, 'pr_review'] into agent_plan at the current position
    so the plan router sends the pipeline back to the coder, then back here."""
    plan = list(state.get("agent_plan", []))
    # Find pr_review's position in the plan
    pr_idx = next((i for i, a in enumerate(plan) if a == "pr_review"), None)
    if pr_idx is None:
        # pr_review not in plan (legacy path) — just set next_agent directly
        state["next_agent"] = coder_agent
        return
    # Replace the pr_review entry with [coder_agent, "pr_review"]
    new_plan = plan[:pr_idx] + [coder_agent, "pr_review"] + plan[pr_idx + 1:]
    state["agent_plan"] = new_plan
    state["agent_plan_index"] = pr_idx   # points to coder_agent now


def _detect_coder_agent(state: PipelineState) -> str:
    """Find which coder agent ran most recently."""
    outputs = state.get("agent_outputs", {})
    for name in reversed(["coder_mobile", "coder_web", "coder_backend", "coder"]):
        if name in outputs:
            return name
    coder_out = state.get("coder_output") or {}
    pt = coder_out.get("project_type", "web")
    return {"android": "coder_mobile", "web": "coder_web", "backend": "coder_backend"}.get(pt, "coder")


def _confidence(verdict: str, iteration: int) -> float:
    if verdict == "approved":
        return 0.92
    if verdict == "changes_requested":
        # Drop confidence with each failed iteration, but stay above 0.6 while looping
        return max(0.65, 0.85 - iteration * 0.08)
    return 0.30  # blocked


# ---------------------------------------------------------------------------
# Code loading
# ---------------------------------------------------------------------------

def _load_code(coder_out: dict) -> tuple[str, str]:
    """Return (code_text, filename). Both empty string on failure."""
    project_type = coder_out.get("project_type", "web")
    local_path = coder_out.get("local_path", "")
    project_dir = coder_out.get("project_dir", "")

    if project_type == "android" and project_dir:
        candidates = list(Path(project_dir).rglob("MainActivity.kt"))
        if candidates:
            return candidates[0].read_text("utf-8", errors="replace"), candidates[0].name

    if project_type == "web" and local_path and Path(local_path).exists():
        return Path(local_path).read_text("utf-8", errors="replace"), "index.html"

    if project_type == "backend" and project_dir:
        main_py = Path(project_dir) / "main.py"
        if main_py.exists():
            return main_py.read_text("utf-8", errors="replace"), "main.py"

    # Generic fallback: try local_path
    if local_path and Path(local_path).exists():
        return Path(local_path).read_text("utf-8", errors="replace"), Path(local_path).name

    return "", ""
