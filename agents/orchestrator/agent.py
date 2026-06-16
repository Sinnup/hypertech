"""
Orchestrator — entry point for every pipeline run.
Classifies intent, sets scenario, updates feature registry, notifies Slack.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langfuse import observe
from core.state.pipeline_state import PipelineState, agent_message
from core.routing.intent_classifier import classify
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import trace_pipeline, get_client

REGISTRY_PATH = Path(get_optional("FEATURE_REGISTRY_PATH", "features/feature-registry.json"))


def _load_registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {}


def _save_registry(registry: dict):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


@observe(name="orchestrator-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]

    slack.status(ticket_id, "🟡 Orchestrator received prompt — classifying intent...")

    # Record input on the current trace span
    client = get_client()
    if client:
        client.set_current_trace_io(input={"prompt": prompt, "ticket_id": ticket_id})

    # Start Langfuse trace for this pipeline run
    trace_pipeline(ticket_id=ticket_id, prompt=prompt)

    # Classify intent
    classification = classify(prompt, ticket_id=ticket_id)
    scenario = classification["scenario"]
    next_agent = classification["starting_agent"]

    slack.status(
        ticket_id,
        f"🔍 Classified as *{scenario.upper()}* — routing to `{next_agent}` "
        f"(confidence: {classification['confidence']:.0%})"
    )

    # Update feature registry
    registry = _load_registry()
    registry[ticket_id] = {
        "title": prompt[:80],
        "scenario": scenario,
        "status": f"routed_to_{next_agent}",
        "created": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "agents_involved": ["orchestrator"],
        "human_approvals": [],
        "branch": f"feature/{ticket_id}",
        "changelog_ref": f"changelogs/{ticket_id}.md",
    }
    _save_registry(registry)

    # Update state
    state["scenario"] = scenario
    state["current_agent"] = "orchestrator"
    state["next_agent"] = next_agent
    state["status"] = f"routed_to_{next_agent}"
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    state["agent_messages"].append(
        agent_message("orchestrator", next_agent, "routing", ticket_id, classification)
    )

    return state
