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
from core.notifications import slack
from core.tracing.langfuse import get_client
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


def _format_kb_hits(hits: list[dict]) -> str:
    if not hits:
        return "No regulatory documents found in the knowledge base."
    parts = []
    for h in hits:
        meta = h.get("metadata", {})
        source = meta.get("source", meta.get("drive_file_id", "unknown"))
        parts.append(f"[{source}]\n{h['document'][:500]}")
    return "\n\n---\n\n".join(parts)


@observe(name="ba-compliance-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]
    scenario = state.get("scenario", "internal")

    slack.status(ticket_id, "📋 BA/Compliance agent started — extracting requirements...")

    # Query KB for relevant regulations
    kb_result = kb.query(prompt, n_results=5)
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

    usage = result.usage_metadata or {}
    if client:
        client.update_current_generation(
            model=get_model_id(ModelTier.BALANCED),
            output=report,
            usage_details={
                "input": usage.get("input_tokens", 0),
                "output": usage.get("output_tokens", 0),
            },
        )

    status_emoji = {"compliant": "✅", "partial": "⚠️", "non_compliant": "❌", "unknown": "❓"}
    emoji = status_emoji.get(report["overall_status"], "❓")
    slack.status(
        ticket_id,
        f"{emoji} Compliance check: *{report['overall_status'].upper()}* — "
        f"{len(report['requirements'])} requirements, "
        f"{len(report['compliance_gaps'])} gaps"
    )

    if report.get("human_approval_required"):
        slack.approval_request(
            ticket_id,
            stage="compliance_review",
            summary=(
                f"*Status:* {report['overall_status']}\n"
                f"*Gaps:* {len(report['compliance_gaps'])}\n"
                f"*Summary:* {report['summary']}"
            ),
        )

    registry_store.update_ticket(ticket_id, {
        "status": "compliance_checked",
        "agents_involved": ["orchestrator", "ba_compliance"],
    })

    state["compliance_report"] = report
    state["current_agent"] = "ba_compliance"
    state["next_agent"] = "ux_ui"
    state["status"] = "compliance_checked"
    state["human_approval_required"] = report.get("human_approval_required", False)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("ba_compliance", "ux_ui", "compliance_ready", ticket_id, report)
    )

    return state
