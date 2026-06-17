"""
Orchestrator — entry point for every pipeline run.
Classifies intent, sets scenario, updates feature registry, notifies Slack.
"""

import uuid
from datetime import datetime, timezone

from langfuse import observe
from core.state.pipeline_state import PipelineState, agent_message
from core.routing.intent_classifier import classify
from core.notifications import slack
from core.secrets.loader import get_optional
from core.tracing.langfuse import get_client
import core.registry as registry_store


@observe(name="orchestrator-agent")
def run(state: PipelineState) -> PipelineState:
    ticket_id = state["ticket_id"]
    prompt = state["human_prompt"]

    slack.status(ticket_id, "🟡 Orchestrator received prompt — classifying intent...")

    client = get_client()
    if client:
        client.update_current_span(input={"prompt": prompt, "ticket_id": ticket_id})

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
    registry_store.create_ticket(ticket_id, {
        "title": prompt[:80],
        "scenario": scenario,
        "status": f"routed_to_{next_agent}",
        "created": datetime.now(timezone.utc).isoformat(),
        "agents_involved": ["orchestrator"],
        "human_approvals": [],
        "branch": f"feature/{ticket_id}",
        "changelog_ref": f"changelogs/{ticket_id}.md",
    })

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
