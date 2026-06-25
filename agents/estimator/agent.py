"""
Estimator agent — turns discovery notes / a free-text scope into a structured,
priced work breakdown for a client proposal.

Part of the Pre-sales / GTM agent set.  Produces a SALES deliverable (an estimate),
not production software, so it sits outside the code-review / security loop.

Design — mirrors the `security` agent's deterministic-tool + LLM-brain split:
  * The LLM ONLY parses free text into ``{task, role, hours}`` items.
  * ALL pricing arithmetic is deterministic Python over a rate-card config
    (``config/gtm/rate_card.yaml``).  The LLM never computes a price, so totals
    are reproducible and auditable — the number a salesperson quotes always
    traces back to the rate card.

Inputs (from ``PipelineState``):
  * ``human_prompt`` — free-text discovery / requirements (parsed by the LLM), OR
  * ``state["estimator_scope"]`` — a pre-structured ``list[{task, role, hours}]``
    that bypasses the LLM (used by tests and when an upstream agent already
    produced a breakdown).
  * ``state["language"]`` — ``"es"`` (default) or ``"en"`` for the rendered output.

Output:
  * ``state["agent_outputs"]["estimator"]`` — AgentOutput envelope (``data`` = estimate)
  * writes ``estimate.json`` + ``estimate.md`` under ``poc/{ticket_id}/proposal/``
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml
from langchain_core.prompts import ChatPromptTemplate
from core.tracing.langfuse import observe

from core.ai import get_llm, get_model_id, parse_json, ModelTier
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.prompt_registry import get_prompt
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.tracing.langfuse import record_generation

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_RATE_CARD_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "gtm" / "rate_card.yaml"

# Fallback system prompt if prompts/estimator/system.txt is missing.
_FALLBACK_SYSTEM = (
    "You are a pre-sales estimator. Decompose the request into work items.\n"
    "Use ONLY these roles: {roles}.\n"
    "Return ONLY a raw JSON array of objects {{\"task\": str, \"role\": str, \"hours\": int}}. "
    "Do not output prices."
)


# ---------------------------------------------------------------------------
# Deterministic pricing (pure — no LLM, fully unit-testable)
# ---------------------------------------------------------------------------

def load_rate_card(path: Path | str | None = None) -> dict:
    """Load and normalize the GTM rate card. Raises FileNotFoundError if absent."""
    p = Path(path) if path else _RATE_CARD_PATH
    if not p.exists():
        raise FileNotFoundError(f"Rate card not found at {p}")
    card = yaml.safe_load(p.read_text("utf-8")) or {}
    card.setdefault("currency", "MXN")
    card.setdefault("contingency_pct", 0)
    card.setdefault("roles", {})
    card.setdefault("default_role", "engineer")
    return card


def price_scope(scope_items: list[dict], rate_card: dict) -> dict:
    """Price a list of ``{task, role, hours}`` items against the rate card.

    Pure function: identical input always yields an identical estimate.
    Unknown roles fall back to ``default_role`` and are flagged in
    ``unknown_roles`` so confidence can be lowered upstream.
    """
    roles = rate_card.get("roles", {})
    default_role = rate_card.get("default_role", "engineer")
    default_rate = roles.get(default_role, 0)
    currency = rate_card.get("currency", "MXN")
    contingency_pct = float(rate_card.get("contingency_pct", 0))

    line_items: list[dict] = []
    unknown_roles: list[str] = []
    subtotal = 0.0
    total_hours = 0

    for item in scope_items:
        role = item.get("role", default_role)
        hours = int(item.get("hours", 0) or 0)
        rate_known = role in roles
        if not rate_known and role not in unknown_roles:
            unknown_roles.append(role)
        rate = roles.get(role, default_rate)
        cost = round(rate * hours, 2)
        subtotal += cost
        total_hours += hours
        line_items.append({
            "task": item.get("task", ""),
            "role": role,
            "hours": hours,
            "hourly_rate": rate,
            "cost": cost,
            "rate_known": rate_known,
        })

    subtotal = round(subtotal, 2)
    contingency = round(subtotal * contingency_pct / 100.0, 2)
    total = round(subtotal + contingency, 2)

    assumptions = [
        f"Rates sourced from rate card ({currency}); not negotiated.",
        f"Contingency buffer of {contingency_pct:g}% applied on the subtotal.",
        "Effort estimated from stated scope; excludes client-side delays.",
    ]
    if unknown_roles:
        assumptions.append(
            f"Role(s) not in rate card priced at '{default_role}' rate: {', '.join(unknown_roles)}."
        )

    return {
        "currency": currency,
        "line_items": line_items,
        "total_hours": total_hours,
        "subtotal": subtotal,
        "contingency_pct": contingency_pct,
        "contingency": contingency,
        "total": total,
        "unknown_roles": unknown_roles,
        "assumptions": assumptions,
    }


# ---------------------------------------------------------------------------
# LLM scope parsing (the only non-deterministic step)
# ---------------------------------------------------------------------------

def _parse_scope(prompt: str, rate_card: dict) -> list[dict]:
    """Use the LLM to turn free-text discovery into ``{task, role, hours}`` items."""
    roles = ", ".join(sorted(rate_card.get("roles", {}).keys()))
    system = get_prompt("estimator", "system") or _FALLBACK_SYSTEM

    chat = ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", "Discovery / requirements:\n{prompt}"),
    ])
    llm = get_llm(tier=ModelTier.BALANCED, temperature=0, max_tokens=2000)
    result = (chat | llm).invoke({"roles": roles, "prompt": prompt})
    record_generation(get_model_id(ModelTier.BALANCED), result, output=getattr(result, "content", ""))

    parsed = parse_json(result.content)
    items = parsed if isinstance(parsed, list) else parsed.get("items", [])
    # Keep only well-formed items.
    clean = []
    for it in items:
        if isinstance(it, dict) and it.get("task") and it.get("hours"):
            clean.append({
                "task": str(it["task"]),
                "role": str(it.get("role", rate_card.get("default_role", "engineer"))),
                "hours": int(it["hours"]),
            })
    return clean


# ---------------------------------------------------------------------------
# Rendering + artifacts
# ---------------------------------------------------------------------------

def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def render_markdown(estimate: dict, ticket_id: str, language: str = "es") -> str:
    cur = estimate["currency"]
    es = language != "en"
    t = {
        "title": "Estimación de Esfuerzo y Costo" if es else "Effort & Cost Estimate",
        "task": "Tarea" if es else "Task",
        "role": "Rol" if es else "Role",
        "hours": "Horas" if es else "Hours",
        "rate": "Tarifa/h" if es else "Rate/h",
        "cost": "Costo" if es else "Cost",
        "subtotal": "Subtotal" if es else "Subtotal",
        "contingency": "Contingencia" if es else "Contingency",
        "total": "Total" if es else "Total",
        "assumptions": "Supuestos" if es else "Assumptions",
        "tothours": "Horas totales" if es else "Total hours",
    }
    lines = [f"# {t['title']} — {ticket_id}", ""]
    lines.append(f"| {t['task']} | {t['role']} | {t['hours']} | {t['rate']} | {t['cost']} |")
    lines.append("|---|---|---:|---:|---:|")
    for li in estimate["line_items"]:
        flag = "" if li["rate_known"] else " *"
        lines.append(
            f"| {li['task']} | {li['role']}{flag} | {li['hours']} | "
            f"{_money(li['hourly_rate'], cur)} | {_money(li['cost'], cur)} |"
        )
    lines += [
        "",
        f"**{t['tothours']}:** {estimate['total_hours']}  ",
        f"**{t['subtotal']}:** {_money(estimate['subtotal'], cur)}  ",
        f"**{t['contingency']} ({estimate['contingency_pct']:g}%):** {_money(estimate['contingency'], cur)}  ",
        f"**{t['total']}:** {_money(estimate['total'], cur)}",
        "",
        f"### {t['assumptions']}",
    ]
    lines += [f"- {a}" for a in estimate["assumptions"]]
    return "\n".join(lines)


def _write_artifacts(ticket_id: str, estimate: dict, language: str) -> dict:
    out_dir = Path(__file__).resolve().parent.parent.parent / "poc" / ticket_id / "proposal"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "estimate.json"
    md_path = out_dir / "estimate.md"
    json_path.write_text(json.dumps(estimate, indent=2, ensure_ascii=False), "utf-8")
    md_path.write_text(render_markdown(estimate, ticket_id, language), "utf-8")
    return {"estimate_json": str(json_path), "estimate_md": str(md_path)}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

@register(
    "estimator",
    description="Discovery notes -> scoped work breakdown + priced estimate (deterministic pricing)",
    tier=ModelTier.BALANCED,
    tags=["gtm", "presales", "estimate"],
)
@observe(name="estimator-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    language = state.get("language", "es")
    slack.status(ticket_id, "📐 Estimator — scoping & pricing...")

    rate_card = load_rate_card()

    # Structured scope bypasses the LLM (tests / upstream breakdown); else parse.
    scope = state.get("estimator_scope")
    if not scope:
        scope = _parse_scope(state.get("human_prompt", ""), rate_card)

    estimate = price_scope(scope, rate_card)

    # Confidence: clean estimate is high; unknown roles or empty scope lower it.
    validation_errors: list[str] = []
    if not scope:
        status, confidence = "failed", 0.3
        validation_errors.append("No scope items could be derived from the input.")
    elif estimate["unknown_roles"]:
        status, confidence = "degraded", 0.62
        validation_errors.append(
            f"Roles not in rate card (priced at default): {', '.join(estimate['unknown_roles'])}."
        )
    else:
        status, confidence = "ok", 0.9

    if scope:
        artifacts = _write_artifacts(ticket_id, estimate, language)
        estimate["artifacts"] = artifacts
        slack.status(
            ticket_id,
            f"💰 Estimate: {_money(estimate['total'], estimate['currency'])} "
            f"({estimate['total_hours']}h, {len(estimate['line_items'])} items)"
        )
    else:
        slack.status(ticket_id, "⚠️ Estimator: could not derive scope — escalating.")

    # Advance to the next planned agent (same pattern as the other agents).
    plan = state.get("agent_plan", [])
    idx = state.get("agent_plan_index", 0)
    next_agent = plan[idx] if plan and idx < len(plan) else None

    state["current_agent"] = "estimator"
    state["next_agent"] = next_agent
    state["status"] = "estimated" if scope else "estimate_failed"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_outputs"]["estimator"] = AgentOutput(
        status=status,
        data=estimate,
        validation_errors=validation_errors,
        confidence=confidence,
        agent_name="estimator",
        summary=f"{_money(estimate['total'], estimate['currency'])} / {estimate['total_hours']}h",
    ).model_dump()
    state["confidence_scores"]["estimator"] = confidence
    state["agent_messages"].append(
        agent_message("estimator", next_agent or "proposal", "estimate_ready", ticket_id,
                      {"total": estimate["total"], "currency": estimate["currency"]})
    )
    return state
