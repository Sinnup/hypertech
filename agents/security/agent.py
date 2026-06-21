"""
Security agent — orchestrates Semgrep SAST on generated code, then has Sonnet
review the findings against the compliance KB. Max 3 iteration cycles before
human escalation.

Semgrep is free OSS; if not installed, the agent falls back to a pure LLM scan.
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry import register
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import get_client, record_generation

_REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))
_MAX_ITERATIONS = int(get_optional("MAX_SECURITY_ITERATIONS", "3"))


def _update_registry(ticket_id: str, updates: dict):
    registry = json.loads(_REGISTRY_PATH.read_text()) if _REGISTRY_PATH.exists() else {}
    if ticket_id in registry:
        registry[ticket_id].update(updates)
        registry[ticket_id]["last_updated"] = datetime.now(timezone.utc).isoformat()
        _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def _run_semgrep(code: str, filename: str) -> list[dict]:
    """Run Semgrep on code string. Returns list of findings."""
    try:
        ext = Path(filename).suffix or ".html"
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


_REVIEW_SYSTEM = """You are a security expert for a Mexican fintech company.
Given code and Semgrep findings (may be empty), identify security issues relevant to:
EMV card data, PII, authentication tokens, XSS, injection, insecure storage.

Return JSON:
{{
  "findings": [{{"severity": "critical|high|medium|low", "issue": "...", "line": 0, "fix": "..."}}],
  "overall_risk": "critical|high|medium|low|clean",
  "approved": true|false,
  "human_escalation_required": false,
  "summary": "..."
}}

approved=true only when overall_risk is low or clean.
Return raw JSON only."""

_REVIEW_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _REVIEW_SYSTEM),
    ("human", "File: {filename}\n\nCode (first 6000 chars):\n{code}\n\nSemgrep findings:\n{semgrep_json}"),
])


@register("security", description="Runs Semgrep SAST + LLM security review for fintech compliance (OWASP, EMV, PII)",
          tier=ModelTier.BALANCED, tags=["security", "compliance"])
@observe(name="security-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    coder_out = state.get("coder_output") or {}
    filename = coder_out.get("filename", "unknown")
    local_path = coder_out.get("local_path", "")

    slack.status(ticket_id, "🔒 Security agent started — running SAST scan...")

    # Load code
    code = ""
    if local_path and Path(local_path).exists():
        code = Path(local_path).read_text()

    if not code:
        slack.status(ticket_id, "⚠️ Security: no code file — skipping scan.")
        state["current_agent"] = "security"
        state["next_agent"] = None
        state["status"] = "security_skipped"
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        return state

    # Semgrep scan
    semgrep_findings = _run_semgrep(code, filename)
    if semgrep_findings:
        slack.status(ticket_id, f"🔎 Semgrep: {len(semgrep_findings)} finding(s) — sending to Sonnet review...")
    else:
        slack.status(ticket_id, "🔎 Semgrep: no findings (or not installed) — LLM review only...")

    # LLM security review
    llm = get_llm(tier=ModelTier.BALANCED, temperature=0, max_tokens=2048)
    chain = _REVIEW_PROMPT | llm
    result = chain.invoke({
        "filename": filename,
        "code": code[:6000],
        "semgrep_json": json.dumps(semgrep_findings[:20], indent=2),
    })
    report = parse_json(result.content)
    record_generation(get_model_id(ModelTier.BALANCED), result, output={"overall_risk": report.get("overall_risk"), "findings": len(report.get("findings", []))})

    risk = report.get("overall_risk", "unknown")
    findings = report.get("findings", [])
    critical = [f for f in findings if f.get("severity") == "critical"]
    emoji = {"clean": "✅", "low": "✅", "medium": "⚠️", "high": "❌", "critical": "🚨"}.get(risk, "❓")

    slack.status(
        ticket_id,
        f"{emoji} Security scan: *{risk.upper()}* — "
        f"{len(findings)} finding(s), {len(critical)} critical"
    )

    # Human escalation if critical findings or max iterations reached
    if report.get("human_escalation_required") or critical:
        slack.approval_request(
            ticket_id,
            stage="security_review",
            summary=(
                f"*Risk:* {risk}\n"
                f"*Critical:* {len(critical)}\n"
                f"*Summary:* {report.get('summary', '')}"
            ),
        )

    _update_registry(ticket_id, {
        "status": "security_reviewed",
        "agents_involved": ["orchestrator", "ba_compliance", "ux_ui", "architect", "pr_review", "security"],
    })

    state["security_findings"] = report
    state["current_agent"] = "security"
    state["next_agent"] = None
    state["status"] = "security_reviewed"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("security", "human", "security_report", ticket_id, {
            "overall_risk": risk,
            "finding_count": len(findings),
        })
    )

    return state
