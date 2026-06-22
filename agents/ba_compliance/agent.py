"""
BA / Compliance agent — extracts requirements from the prompt and validates
them against Mexican fintech regulations stored in ChromaDB.

Uses the BALANCED model tier for analysis; falls back gracefully when KB is
empty (Drive not yet ingested) by generating requirements from the prompt alone.
"""

from datetime import datetime, timezone

from langchain_core.prompts import ChatPromptTemplate
from langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.circuit_breaker import circuit_breaker
from core.notifications import slack
from core.tracing.langfuse import get_client, record_generation
from agents.knowledge_base import agent as kb
import core.registry as registry_store

_SYSTEM = """You are a Business Analyst and Compliance expert for a Mexican fintech company.
Given a product description and optional regulatory context, produce a structured JSON output:
{{
  "requirements": [{{"id": "REQ-001", "description": "...", "priority": "must|should|nice"}}],
  "compliance_gaps": [{{"regulation": "...", "gap": "...", "severity": "high|medium|low"}}],
  "compliance_passed": [{{"regulation": "...", "note": "..."}}],
  "overall_status": "compliant|partial|non_compliant|unknown",
  "human_approval_required": true|false,
  "summary": "One paragraph summary"
}}

Regulations to check when context is available: EMV (card payments), LACP (AML/KYC),
CNBV regulations, NOM-151 (electronic signatures), PCI-DSS basics.

If no regulatory context is provided, mark overall_status as "unknown" and
human_approval_required as true.

Return raw JSON only, no markdown."""

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", "Product description: {prompt}\n\nRegulatory context:\n{context}"),
])


def _kb_fallback(prompt: str, n_results: int = 5) -> dict:
    """Fallback: return empty KB results when ChromaDB is unreachable."""
    slack.alert("⚠️ ChromaDB unavailable — running compliance check without regulatory context.")
    return {"hits": [], "empty": True}


@circuit_breaker(
    service_name="chromadb",
    fallback=_kb_fallback,
    fallback_label="Run without regulatory context (last-known-good state)",
    timeout=10.0,
)
def _query_kb(prompt: str, n_results: int = 5) -> dict:
    """Query the knowledge base with circuit breaker protection."""
    return kb.query(prompt, n_results=n_results)


def _format_kb_hits(hits: list) -> str:
    """Format KB hit documents into a context string for the LLM prompt."""
    if not hits:
        return "No regulatory documents found in the knowledge base."
    parts = []
    for h in hits:
        meta = h.get("metadata", {})
        source = meta.get("source", meta.get("drive_file_id", "unknown"))
        parts.append(f"[{source}]\n{h['document'][:500]}")
    return "\n\n---\n\n".join(parts)


@register("ba_compliance", description="Extracts requirements, validates against fintech regulations via ChromaDB RAG",
          tier=ModelTier.BALANCED, tags=["analysis", "compliance", "rag"])
@observe(name="ba-compliance-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    scenario = state.get("scenario", "internal")

    slack.status(ticket_id, "📋 BA/Compliance agent started — extracting requirements...")

    # Query KB for relevant regulations
    kb_result = _query_kb(prompt, n_results=5)
    context = _format_kb_hits(kb_result["hits"])

    if kb_result["empty"]:
        slack.status(ticket_id, "⚠️ KB is empty — compliance check will run without regulation docs.")

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "kb_hits": len(kb_result["hits"])})

    llm = get_llm(tier=ModelTier.BALANCED, temperature=0.2)
    chain = _PROMPT | llm
    result = chain.invoke({"prompt": prompt, "context": context})
    report = parse_json(result.content)
    record_generation(get_model_id(ModelTier.BALANCED), result, output=report)

    status_emoji = {"compliant": "✅", "partial": "⚠️", "non_compliant": "❌", "unknown": "❓"}
    emoji = status_emoji.get(report["overall_status"], "❓")

    # ── Determine if human approval should BLOCK the pipeline ────────────
    kb_empty = kb_result["empty"]
    needs_approval = report.get("human_approval_required", False)
    force_escalation = False

    if kb_empty and scenario != "poc":
        # No regulatory docs available — compliance check is unreliable.
        # Force human review before continuing.
        report["overall_status"] = "unknown"
        report["human_approval_required"] = True
        report.setdefault("compliance_gaps", []).append({
            "regulation": "KNOWLEDGE_BASE_EMPTY",
            "gap": "No regulatory documents found in ChromaDB. "
                   "Run /reload-kb to ingest documents from Google Drive, "
                   "then resume with /resume.",
            "severity": "high",
        })
        needs_approval = True
        force_escalation = True
        emoji = "❓"

    if needs_approval:
        slack.approval_request(
            ticket_id,
            stage="compliance_review",
            summary=(
                f"*Status:* {report['overall_status']}\n"
                f"*Gaps:* {len(report['compliance_gaps'])}\n"
                f"{'*⚠️ KB EMPTY — no regulatory docs ingested*' if kb_empty else ''}\n"
                f"*Summary:* {report['summary']}"
            ),
        )

    slack.status(
        ticket_id,
        f"{emoji} Compliance check: *{report['overall_status'].upper()}* — "
        f"{len(report['requirements'])} requirements, "
        f"{len(report['compliance_gaps'])} gaps"
        f"{' (KB empty — human review needed)' if kb_empty else ''}"
    )

    registry_store.update_ticket(ticket_id, {
        "status": "compliance_checked",
        "agents_involved": ["orchestrator", "ba_compliance"],
    })

    state["compliance_report"] = report
    state["current_agent"] = "ba_compliance"
    state["next_agent"] = "ux_ui"
    state["status"] = "compliance_checked"
    state["human_approval_required"] = needs_approval
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("ba_compliance", "ux_ui", "compliance_ready", ticket_id, report)
    )

    # ── Store structured AgentOutput with potentially reduced confidence ─
    confidence = report.get("confidence", 0.5)
    if force_escalation:
        confidence = min(confidence, 0.3)  # Force validation_gate → human_escalation
    elif needs_approval:
        confidence = min(confidence, 0.5)  # Below threshold, will escalate

    state["agent_outputs"]["ba_compliance"] = AgentOutput(
        status="degraded" if needs_approval else "ok",
        data=report,
        validation_errors=(
            ["Knowledge base empty — regulatory documents not ingested. "
             "Run /reload-kb and /resume when ready."]
            if kb_empty else []
        ),
        confidence=confidence,
        agent_name="ba_compliance",
    ).model_dump()

    return state
