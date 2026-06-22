"""
Orchestrator — entry point for every pipeline run.
Classifies intent, builds a dynamic agent execution plan, and (in demo mode)
asks clarifying questions via Slack attributed to downstream agents.
"""

import logging
from datetime import datetime, timezone

from langfuse import observe
from core.ai import ModelTier, get_llm, get_model_id, parse_json
from core.agent_registry import register
from core.agent_registry.models import AgentOutput
from core.prompt_registry import get_prompt
from core.routing.intent_classifier import classify
from core.state.pipeline_state import PipelineState, agent_message
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import get_client, record_generation
import core.registry as registry_store


# ---------------------------------------------------------------------------
# Static plan templates — extended by dynamic detection at runtime
# ---------------------------------------------------------------------------

_PLANS = {
    # poc is fully overridden by _build_plan — this is just the generic fallback
    "poc": ["orchestrator", "coder", "infra"],
    # internal: BA → design → code → test → deploy
    "internal": [
        "orchestrator", "ba_compliance", "ux_ui",
        "design_synthesizer", "{coder}", "test_generator", "infra",
    ],
    # production: full pipeline with review + security loops + CI/CD
    "production": [
        "orchestrator", "ba_compliance", "ux_ui",
        "architect", "design_synthesizer", "{coder}",
        "pr_review", "security", "test_generator", "devops", "infra",
    ],
}

# Keyword sets for prompt-based specialization
_MOBILE_KEYWORDS = [
    "android", "ios", "mobile app", "apk", "react native",
    "kotlin", "swift", "flutter", "kmp", "jetpack compose",
    "tpv", "pos", "point of sale", "card payment", "payment terminal",
    "terminal de pago", "cobro", "tarjeta",
]

_WEB_KEYWORDS = [
    "website", "web app", "web application", "dashboard", "landing page",
    "portal", "frontend", "react", "next.js", "vue", "html", "browser",
    "spa", "pwa",
]

_BACKEND_KEYWORDS = [
    "api", "rest api", "backend", "server", "microservice", "fastapi",
    "endpoint", "database", "crud", "service", "worker",
]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@register(
    "orchestrator",
    description="Lead agent — intent classification and dynamic agent planning",
    tier=ModelTier.FAST,
    tags=["routing", "entry"],
)
@observe(name="orchestrator-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]

    # ── Resume mode: skip classification, planning, and registry creation ──
    if state.get("resumed_from_checkpoint"):
        logging.getLogger(__name__).info(
            "Resume mode for %s — skipping classification (plan_index=%s, completed=%s)",
            ticket_id,
            state.get("agent_plan_index"),
            list(state.get("agent_outputs", {}).keys()),
        )

        # Update feature registry to reflect resume
        registry_store.update_ticket(ticket_id, {
            "status": f"resumed_at_index_{state.get('agent_plan_index', 0)}",
            "resumed": True,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

        state["status"] = f"resumed_at_agent_{state.get('agent_plan_index', 0)}"
        state["last_updated"] = datetime.now(timezone.utc).isoformat()

        slack.status(
            ticket_id,
            f"♻️ Resumed from checkpoint — continuing at agent "
            f"#{state.get('agent_plan_index', 0)}",
        )
        return state

    # ── Normal (non-resume) path ─────────────────────────────────────────
    prompt = state["human_prompt"]

    slack.status(ticket_id, "🟡 Orchestrator received prompt — classifying intent...")

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "ticket_id": ticket_id})

    # ---- Step 1: classify intent ------------------------------------------
    classification = classify(prompt, ticket_id=ticket_id)
    scenario = classification["scenario"]
    confidence = classification.get("confidence", 0.90)

    slack.status(
        ticket_id,
        f"🔍 Classified as *{scenario.upper()}* "
        f"(confidence: {confidence:.0%})"
    )

    # ---- Step 2: build dynamic agent plan ---------------------------------
    agent_plan = _build_plan(scenario, prompt)
    demo_questions = _detect_demo_questions(scenario, prompt, agent_plan)

    # ---- Step 3: ask clarifying questions in demo mode --------------------
    if demo_questions:
        _ask_demo_questions(ticket_id, demo_questions)

    # ---- Step 4: update feature registry ----------------------------------
    registry_store.create_ticket(ticket_id, {
        "title": prompt[:80],
        "scenario": scenario,
        "status": f"planned_{len(agent_plan)}_agents",
        "created": datetime.now(timezone.utc).isoformat(),
        "agents_involved": ["orchestrator"],
        "human_approvals": [],
        "branch": f"feature/{ticket_id}",
        "changelog_ref": f"changelogs/{ticket_id}.md",
    })

    # ---- Step 5: write state ----------------------------------------------
    first_agent = agent_plan[1] if len(agent_plan) > 1 else None

    state["scenario"] = scenario
    state["current_agent"] = "orchestrator"
    state["next_agent"] = first_agent          # legacy routing fallback
    state["agent_plan"] = agent_plan            # dynamic plan (new path)
    state["agent_plan_index"] = 0               # orchestrator just completed at plan[0]
    state["status"] = f"planned_{len(agent_plan)}_agents"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    state["agent_messages"].append(
        agent_message("orchestrator", first_agent or "none", "plan_ready", ticket_id, {
            "scenario": scenario,
            "agent_plan": agent_plan,
            "confidence": confidence,
        })
    )

    # ---- Step 6: store structured output ----------------------------------
    state["agent_outputs"]["orchestrator"] = AgentOutput(
        status="ok",
        data={
            "scenario": scenario,
            "agent_plan": agent_plan,
            "confidence": confidence,
            "reason": classification.get("reason", ""),
            "demo_questions": demo_questions,
        },
        confidence=confidence,
        agent_name="orchestrator",
    ).model_dump()

    return state


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------

def _build_plan(scenario: str, prompt: str) -> list[str]:
    """Build the agent execution plan for this scenario.

    All scenarios use keyword-based coder selection.
    POC: short path (code → infra only).
    Internal: BA → design → code → test → infra.
    Production: full pipeline with review + security + CI/CD loops.
    """
    coder = _pick_coder(prompt)

    if scenario == "poc":
        return ["orchestrator", coder, "infra"]

    # Expand the template — replace "{coder}" placeholder
    template = list(_PLANS.get(scenario, _PLANS["poc"]))
    return ["orchestrator"] + [coder if a == "{coder}" else a for a in template[1:]]


def _pick_coder(prompt: str) -> str:
    """Select the right coder agent based on prompt keywords."""
    p = prompt.lower()
    if any(kw in p for kw in _MOBILE_KEYWORDS):
        return "coder_mobile"
    if any(kw in p for kw in _BACKEND_KEYWORDS) and not any(kw in p for kw in _WEB_KEYWORDS):
        return "coder_backend"
    if any(kw in p for kw in _WEB_KEYWORDS):
        return "coder_web"
    return "coder"


# ---------------------------------------------------------------------------
# Demo-mode clarifying questions
# ---------------------------------------------------------------------------

def _detect_demo_questions(
    scenario: str,
    prompt: str,
    plan: list[str],
) -> list[dict]:
    """Return clarifying questions for demo/poc scenarios.

    Questions are attributed to the agent that would ask them so the
    orchestrator can say "Designer asks: Material Design 3 or Liquid Glass?"
    """
    if scenario != "poc":
        return []  # QA/Production is more cautious — agents ask individually

    questions = []
    prompt_lower = prompt.lower()

    # Mobile app questions
    if any(kw in prompt_lower for kw in _MOBILE_KEYWORDS):
        if "designer" in plan or "ux_ui" in plan:
            questions.append({
                "from_agent": "designer",
                "question": "Material Design 3 or Liquid Glass UI toolkit for this Android app?",
            })
        questions.append({
            "from_agent": "architect",
            "question": "Should this TPV app target Android only (POC) or plan for KMP (scaled)?",
        })
        questions.append({
            "from_agent": "coder",
            "question": "Generate a native Android APK (Kotlin/Jetpack Compose) or a PWA/web-based TPV?",
        })

    # General UI questions for any POC
    if not questions:
        questions.append({
            "from_agent": "designer",
            "question": f"Any preference for UI style (Material Design, minimal, or Tailwind clean)?",
        })

    return questions[:3]  # max 3 questions — don't overwhelm


def _ask_demo_questions(ticket_id: str, questions: list[dict]) -> None:
    """Post clarifying questions to Slack, attributed to downstream agents."""
    for q in questions:
        agent = q["from_agent"]
        question = q["question"]
        slack.alert(
            f"💬 *{agent.title()} asks:* {question}\n"
            f"(Reply in thread or via `/new` with clarifications)",
            channel="agent-status",
        )
