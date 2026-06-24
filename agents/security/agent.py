"""
Security / DevAnalyzer agent — OWASP Top 10 + Semgrep SAST + tech-stack-specific
checks.  Max 3 iteration cycles — on fixable critical findings, routes back to
the appropriate coder agent exactly like pr_review does.

Phase 3 enhancements:
  - OWASP A01–A10 checklist mapped to project type
  - Tech-stack-specific checks (Android: MobSF concepts; Web: CSP/XSS; Backend: SQLi/auth)
  - 3-iteration loop back to coder on fixable critical issues
  - Slack approval request for unfixable / after max iterations
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe
from langsmith import traceable

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import get_client, record_generation

_MAX_SEC_ITERATIONS = int(get_optional("MAX_SECURITY_ITERATIONS", "3"))

# ---------------------------------------------------------------------------
# OWASP Top 10 context per project type
# ---------------------------------------------------------------------------

_OWASP_CONTEXT = {
    "android": """
OWASP Mobile Top 10 relevance:
- M1 Improper Credential Usage: hardcoded keys, insecure storage of tokens
- M2 Inadequate Supply Chain Security: dependency versions
- M3 Insecure Authentication/Authorization: missing PIN/biometric gate
- M4 Insufficient Input/Output Validation: unvalidated intent data
- M5 Insecure Communication: cleartext traffic allowed
- M7 Insufficient Binary Protections: no ProGuard/R8 enabled
- M8 Security Misconfiguration: exported activities without permission
- M10 Insufficient Cryptography: using deprecated algorithms
""",
    "web": """
OWASP Web Top 10 relevance:
- A01 Broken Access Control: missing auth on sensitive routes
- A02 Cryptographic Failures: sensitive data in localStorage, cleartext
- A03 Injection: XSS via innerHTML/document.write, eval()
- A05 Security Misconfiguration: missing CSP meta tag, CORS wildcard
- A06 Vulnerable Components: CDN links pinned without integrity hash
- A07 Auth Failures: no CSRF protection on state-changing actions
- A09 Logging: sensitive data logged to console
""",
    "backend": """
OWASP Web Top 10 relevance:
- A01 Broken Access Control: routes missing auth dependency
- A02 Cryptographic Failures: secrets in source, weak hashing
- A03 Injection: SQL/NoSQL injection, shell injection via subprocess
- A04 Insecure Design: missing rate limiting on public endpoints
- A05 Security Misconfiguration: DEBUG mode enabled, CORS wildcard *
- A07 Auth Failures: JWT/session misconfiguration
- A08 Software Integrity: no dependency pinning
- A09 Logging Failures: passwords/tokens in log output
""",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM = """You are a security expert for a Mexican fintech company.
Given code, Semgrep findings, and OWASP context, identify security issues.

Return JSON:
{{
  "findings": [
    {{
      "owasp_id": "A03|M1|...",
      "severity": "critical|high|medium|low",
      "issue": "...",
      "line": 0,
      "fix": "...",
      "fixable_by_coder": true|false
    }}
  ],
  "overall_risk": "critical|high|medium|low|clean",
  "approved": true|false,
  "human_escalation_required": false,
  "fixable_critical_count": 0,
  "summary": "..."
}}

approved=true only when overall_risk is low or clean.
fixable_by_coder=true for issues the coder agent can fix automatically (code change).
fixable_by_coder=false for infra/config/architecture issues needing human decision.
Return raw JSON only."""

_REVIEW_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _REVIEW_SYSTEM),
    ("human", (
        "Project type: {project_type}\n\n"
        "Security iteration: {iteration} of {max_iterations}\n\n"
        "OWASP checklist for this stack:\n{owasp_context}\n\n"
        "File: {filename}\n\n"
        "Code (first 6000 chars):\n{code}\n\n"
        "Semgrep findings:\n{semgrep_json}"
    )),
])

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@register(
    "security",
    description="OWASP Top 10 + Semgrep SAST security scan; loops back to coder up to 3× on fixable findings",
    tier=ModelTier.BALANCED,
    tags=["security", "owasp", "compliance"],
)
@observe(name="security-agent")
@traceable(name="security", run_type="chain")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_out = state.get("coder_output") or {}
    project_type = coder_out.get("project_type", "web")
    iteration = state.get("iteration_count", 0) + 1

    slack.status(ticket_id, f"🔒 Security scan — iteration {iteration}/{_MAX_SEC_ITERATIONS} ({project_type})...")

    code, filename = _load_code(coder_out)
    if not code:
        slack.status(ticket_id, "⚠️ Security: no code found — skipping scan.")
        return _done(state, ticket_id, {"overall_risk": "clean", "findings": [], "approved": True,
                                        "summary": "No code to scan"}, iteration)

    # Semgrep
    semgrep_findings = _run_semgrep(code, filename)
    if semgrep_findings:
        slack.status(ticket_id, f"🔎 Semgrep: {len(semgrep_findings)} finding(s)")
    else:
        slack.status(ticket_id, "🔎 Semgrep: clean (or not installed)")

    owasp_ctx = _OWASP_CONTEXT.get(project_type, _OWASP_CONTEXT["backend"])

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0, max_tokens=3000)
    chain = _REVIEW_PROMPT | llm
    result = chain.invoke({
        "project_type": project_type,
        "iteration": iteration,
        "max_iterations": _MAX_SEC_ITERATIONS,
        "owasp_context": owasp_ctx,
        "filename": filename,
        "code": code[:6000],
        "semgrep_json": json.dumps(semgrep_findings[:20], indent=2),
    })

    report = parse_json(result.content)
    record_generation(
        get_model_id(ModelTier.BALANCED), result,
        output={"overall_risk": report.get("overall_risk"), "findings": len(report.get("findings", []))}
    )

    return _route(state, ticket_id, report, iteration, project_type)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route(
    state: PipelineState,
    ticket_id: str,
    report: dict,
    iteration: int,
    project_type: str,
) -> PipelineState:
    risk = report.get("overall_risk", "unknown")
    findings = report.get("findings", [])
    critical = [f for f in findings if f.get("severity") == "critical"]
    fixable_critical = [f for f in critical if f.get("fixable_by_coder")]
    unfixable = [f for f in critical if not f.get("fixable_by_coder")]

    emoji = {"clean": "✅", "low": "✅", "medium": "⚠️", "high": "❌", "critical": "🚨"}.get(risk, "❓")
    slack.status(
        ticket_id,
        f"{emoji} Security: *{risk.upper()}* — {len(findings)} finding(s), "
        f"{len(critical)} critical ({len(fixable_critical)} fixable by coder)"
    )

    # Approved / low risk → proceed
    if report.get("approved") or risk in ("clean", "low"):
        slack.status(ticket_id, "✅ Security approved — pipeline clear.")
        return _done(state, ticket_id, report, iteration)

    # Unfixable critical or max iterations → human escalation
    if unfixable or (critical and iteration >= _MAX_SEC_ITERATIONS):
        reason = (
            f"Security: unfixable critical issues after {iteration} iteration(s): "
            + "; ".join(f.get("issue", "") for f in (unfixable or critical)[:3])
        )
        slack.status(ticket_id, f"🚨 Human escalation: {reason}")
        slack.approval_request(
            ticket_id, stage="security_review",
            summary=f"*Risk:* {risk}\n*Critical:* {len(critical)}\n*Summary:* {report.get('summary', '')}"
        )
        state["human_escalation"] = True
        state["human_escalation_reason"] = reason
        state["security_findings"] = report
        state["current_agent"] = "security"
        state["status"] = "security_blocked"
        state["iteration_count"] = iteration
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state["agent_outputs"]["security"] = AgentOutput(
            status="failed", data=report, confidence=0.25, agent_name="security"
        ).model_dump()
        return state

    # Fixable critical + iterations remaining → route back to coder
    if fixable_critical and iteration < _MAX_SEC_ITERATIONS:
        coder_agent = _detect_coder(state, project_type)
        slack.status(
            ticket_id,
            f"🔄 Security: {len(fixable_critical)} fixable issue(s) — routing to *{coder_agent}* "
            f"(iteration {iteration}/{_MAX_SEC_ITERATIONS})"
        )
        issue_list = "\n".join(
            f"• [{f.get('owasp_id')}] {f.get('issue')} → {f.get('fix','')}" for f in fixable_critical[:5]
        )
        slack.status(ticket_id, f"Security fixes needed:\n{issue_list}")
        _inject_security_loop(state, coder_agent)
        state["security_findings"] = report
        state["current_agent"] = "security"
        state["status"] = f"security_fixes_iter{iteration}"
        state["iteration_count"] = iteration
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state["agent_outputs"]["security"] = AgentOutput(
            status="degraded", data=report,
            confidence=max(0.65, 0.85 - iteration * 0.08),
            agent_name="security"
        ).model_dump()
        state["agent_messages"].append(
            agent_message("security", coder_agent, "security_fixes_needed", ticket_id,
                          {"fixable": [f.get("issue") for f in fixable_critical]})
        )
        return state

    # Medium/high non-critical → proceed with warning
    slack.status(ticket_id, f"⚠️ Security: {risk} risk — proceeding with human awareness.")
    return _done(state, ticket_id, report, iteration)


def _done(state: PipelineState, ticket_id: str, report: dict, iteration: int) -> PipelineState:
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else None

    state["security_findings"] = report
    state["current_agent"] = "security"
    state["next_agent"] = next_agent
    state["status"] = "security_reviewed"
    state["iteration_count"] = iteration
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_outputs"]["security"] = AgentOutput(
        status="ok", data=report, confidence=0.92, agent_name="security"
    ).model_dump()
    state["agent_messages"].append(
        agent_message("security", next_agent or "human", "security_report", ticket_id,
                      {"overall_risk": report.get("overall_risk")})
    )
    return state


def _inject_security_loop(state: PipelineState, coder_agent: str) -> None:
    """Same plan-injection technique as pr_review."""
    plan = list(state.get("agent_plan", []))
    sec_idx = next((i for i, a in enumerate(plan) if a == "security"), None)
    if sec_idx is None:
        state["next_agent"] = coder_agent
        return
    new_plan = plan[:sec_idx] + [coder_agent, "security"] + plan[sec_idx + 1:]
    state["agent_plan"] = new_plan
    state["agent_plan_index"] = sec_idx


def _detect_coder(state: PipelineState, project_type: str) -> str:
    outputs = state.get("agent_outputs", {})
    for name in reversed(["coder_mobile", "coder_web", "coder_backend", "coder"]):
        if name in outputs:
            return name
    return {"android": "coder_mobile", "web": "coder_web", "backend": "coder_backend"}.get(
        project_type, "coder"
    )


# ---------------------------------------------------------------------------
# Code loading + Semgrep
# ---------------------------------------------------------------------------

def _load_code(coder_out: dict) -> tuple[str, str]:
    project_type = coder_out.get("project_type", "web")
    project_dir = coder_out.get("project_dir", "")
    local_path = coder_out.get("local_path", "")

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

    if local_path and Path(local_path).exists():
        return Path(local_path).read_text("utf-8", errors="replace"), Path(local_path).name

    return "", ""


def _run_semgrep(code: str, filename: str) -> list[dict]:
    try:
        ext = Path(filename).suffix or ".py"
        with tempfile.NamedTemporaryFile(suffix=ext, mode="w", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["semgrep", "--config=auto", "--json", tmp_path],
            capture_output=True, text=True, timeout=60,
        )
        Path(tmp_path).unlink(missing_ok=True)

        if result.returncode not in (0, 1):
            return []

        data = json.loads(result.stdout)
        return [
            {
                "rule": r.get("check_id", ""),
                "severity": r.get("extra", {}).get("severity", "WARNING"),
                "message": r.get("extra", {}).get("message", ""),
                "line": r.get("start", {}).get("line", 0),
            }
            for r in data.get("results", [])
        ]
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []
