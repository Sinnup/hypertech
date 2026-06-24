"""
Graph structure definition and event-emission helpers.

Builds the DAG layout — node positions, edges, and scenario paths — dynamically
from the agent registry.  Infrastructure nodes (validation_gate, context_packer,
human_escalation) are added automatically with computed positions.

Events are emitted by iterating ``graph.stream(state, stream_mode="updates")``
(LangGraph's built-in streaming) — no custom node wrappers needed.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Iterator

logger = logging.getLogger(__name__)

# Port where the viz server (or Slack command server with viz Blueprint) listens.
_VIZ_PORT = int(os.getenv("VIZ_PORT", os.getenv("SLACK_COMMANDS_PORT", "8080")))
_INGEST_URL = f"http://localhost:{_VIZ_PORT}/viz/ingest"

# ---------------------------------------------------------------------------
# Dynamic graph layout
# ---------------------------------------------------------------------------

# Layout constants
_LEFT_COL_X = 180
_RIGHT_COL_X = 620
_CENTER_X = 400
_BASE_Y = 50
_ROW_HEIGHT = 130
_INFRA_ROW_OFFSET = 160  # extra space before infra nodes

# Agent → column assignment
# Left: code-generation and deployment agents
# Right: analysis, design, quality, and DevOps agents
_LEFT_COLUMN = {
    "coder", "coder_mobile", "coder_web", "coder_backend", "infra",
    "test_generator", "devops",
}
_RIGHT_COLUMN = {
    "ba_compliance", "ux_ui", "architect", "design_synthesizer",
    "pr_review", "security",
}


def _build_graph_structure() -> dict:
    """Build the graph layout dynamically from the agent registry."""
    from core.agent_registry import discover_agents, get_visible_agents

    discover_agents()
    agents = get_visible_agents()

    # -- Build agent nodes ---------------------------------------------------
    nodes = []
    left_row = 0
    right_row = 0

    # Orchestrator always centered at top
    nodes.append({
        "id": "orchestrator", "label": "Orchestrator",
        "x": _CENTER_X, "y": _BASE_Y, "row": 0,
    })

    for name in sorted(agents):
        if name == "orchestrator":
            continue
        if name in _LEFT_COLUMN:
            y = _BASE_Y + _ROW_HEIGHT + left_row * _ROW_HEIGHT
            nodes.append({
                "id": name, "label": _label(name),
                "x": _LEFT_COL_X, "y": y, "row": left_row + 1,
            })
            left_row += 1
        elif name in _RIGHT_COLUMN:
            y = _BASE_Y + _ROW_HEIGHT + right_row * _ROW_HEIGHT
            nodes.append({
                "id": name, "label": _label(name),
                "x": _RIGHT_COL_X, "y": y, "row": right_row + 1,
            })
            right_row += 1
        else:
            # New/unknown agents — place on the right side
            y = _BASE_Y + _ROW_HEIGHT + right_row * _ROW_HEIGHT
            nodes.append({
                "id": name, "label": _label(name),
                "x": _RIGHT_COL_X, "y": y, "row": right_row + 1,
            })
            right_row += 1

    max_row = max(left_row, right_row, 1)

    # -- Infrastructure nodes (below all agents) ------------------------------
    infra_y = _BASE_Y + _ROW_HEIGHT + max_row * _ROW_HEIGHT + _INFRA_ROW_OFFSET

    nodes.append({
        "id": "validation_gate", "label": "Validation Gate",
        "x": _CENTER_X - 120, "y": infra_y, "row": max_row + 10,
    })
    nodes.append({
        "id": "context_packer", "label": "Context Packer",
        "x": _CENTER_X + 120, "y": infra_y, "row": max_row + 10,
    })
    nodes.append({
        "id": "human_escalation", "label": "Human Escalation",
        "x": _CENTER_X, "y": infra_y + _ROW_HEIGHT, "row": max_row + 11,
    })

    # -- Edges ---------------------------------------------------------------
    edges = []

    # Orchestrator → validation_gate (always)
    edges.append({"from": "orchestrator", "to": "validation_gate", "paths": [], "label": ""})

    # Every agent → validation_gate
    for node in nodes:
        nid = node["id"]
        if nid not in ("orchestrator", "validation_gate", "context_packer", "human_escalation"):
            edges.append({"from": nid, "to": "validation_gate", "paths": [], "label": ""})

    # validation_gate → context_packer
    edges.append({"from": "validation_gate", "to": "context_packer", "paths": [], "label": "ok"})
    # validation_gate → human_escalation
    edges.append({"from": "validation_gate", "to": "human_escalation", "paths": [], "label": "low conf"})

    # context_packer → back to agents (generic — frontend shows as fan-out)
    edges.append({"from": "context_packer", "to": "__agents__", "paths": [], "label": "next"})

    # human_escalation → END
    edges.append({"from": "human_escalation", "to": "__end__", "paths": [], "label": ""})

    # -- Scenarios (representative paths for dashboard highlighting) ----------
    scenarios = {
        "poc_mobile": ["orchestrator", "coder_mobile", "infra"],
        "poc_web": ["orchestrator", "coder_web", "infra"],
        "poc_backend": ["orchestrator", "coder_backend", "infra"],
        "poc": ["orchestrator", "coder", "infra"],
        "internal": [
            "orchestrator", "ba_compliance", "ux_ui",
            "design_synthesizer", "coder_web", "test_generator", "infra",
        ],
        "production": [
            "orchestrator", "ba_compliance", "ux_ui", "architect",
            "design_synthesizer", "coder_mobile",
            "pr_review", "security", "test_generator", "devops", "infra",
        ],
    }

    # Add all known agents as a "dynamic" scenario
    agent_ids = [n["id"] for n in nodes if n["id"] not in ("__end__", "__agents__")]
    scenarios["all"] = agent_ids

    return {"nodes": nodes, "edges": edges, "scenarios": scenarios}


def _label(agent_id: str) -> str:
    """Human-readable label from agent ID."""
    labels = {
        "orchestrator": "Orchestrator",
        "coder": "Coder",
        "coder_mobile": "Coder (Mobile)",
        "coder_web": "Coder (Web)",
        "coder_backend": "Coder (Backend)",
        "infra": "Infra / Deploy",
        "ba_compliance": "BA & Compliance",
        "ux_ui": "UX / UI",
        "architect": "Architect",
        "design_synthesizer": "Design Synthesizer",
        "pr_review": "PR Review",
        "security": "Security / OWASP",
        "test_generator": "Test Generator",
        "devops": "DevOps / CI-CD",
        "context_packer": "Context Packer",
        "validation_gate": "Validation Gate",
        "human_escalation": "Human Escalation",
    }
    return labels.get(agent_id, agent_id.replace("_", " ").title())


# Lazy-built cache
_GRAPH_CACHE: dict | None = None


def get_graph_structure() -> dict:
    """Return the full graph layout dict (nodes, edges, scenarios).

    Built lazily from the agent registry and cached for the lifetime of the process.
    """
    global _GRAPH_CACHE
    if _GRAPH_CACHE is None:
        _GRAPH_CACHE = _build_graph_structure()
    return _GRAPH_CACHE


def _path_for_scenario(scenario: str) -> list[str]:
    return get_graph_structure()["scenarios"].get(scenario, [])


# ---------------------------------------------------------------------------
# Cross-process bridge — fire-and-forget HTTP POST to the viz ingest endpoint
# ---------------------------------------------------------------------------

def _post_event(ticket_id: str, event_type: str, data: dict | None = None) -> None:
    """HTTP POST an event to the viz server.  Silently ignores failures."""
    payload = json.dumps({
        "type": event_type,
        "data": {"ticket_id": ticket_id, **(data or {})},
    }).encode()
    req = urllib.request.Request(
        _INGEST_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2)  # nosemgrep — internal localhost
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit_event(ticket_id: str, event_type: str, data: dict | None = None) -> None:
    """Publish an event to both the in-process event_bus AND the HTTP ingest."""
    from .event_bus import event_bus
    event_bus.publish(ticket_id, event_type, data)
    _post_event(ticket_id, event_type, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stream_pipeline(
    graph,
    state: dict,
    ticket_id: str,
    recursion_limit: int = 35,
) -> dict:
    """
    Run the pipeline via LangGraph's native ``graph.stream()``, emitting
    viz events from each chunk.  Saves checkpoints after each agent cycle
    so partial progress survives crashes.

    This replaces ``graph.invoke(state)`` — the pipeline behaves identically
    but we get per-node updates from LangGraph without any custom wrappers.

    Returns the final PipelineState dict.
    """
    from .event_bus import event_bus
    from core.checkpoint.manager import save as save_checkpoint
    from core.checkpoint.manager import delete as delete_checkpoint

    # -- detect resume -----------------------------------------------------
    is_resume = state.get("resumed_from_checkpoint", False)

    # -- pipeline_start (or pipeline_resume) --------------------------------
    emit_event(ticket_id, "pipeline_start" if not is_resume else "pipeline_resume", {
        "ticket_id": ticket_id,
        "prompt": state.get("human_prompt", ""),
        "graph": get_graph_structure(),
        "resumed": is_resume,
    })

    final_state = dict(state)
    prev_node = None

    try:
        # -- iterate LangGraph stream ------------------------------------------
        # stream_mode="updates" yields {node_name: state_update} after each node.
        for chunk in graph.stream(
            state,
            stream_mode="updates",
            config={"recursion_limit": recursion_limit},
        ):
            for node_name, node_output in chunk.items():
                # Set previous node to completed (if any)
                if prev_node:
                    emit_event(ticket_id, "agent_end", {
                        "agent": prev_node,
                        "status": final_state.get("status", "unknown"),
                        "timestamp": _now(),
                    })

                # Mark this node as started
                emit_event(ticket_id, "agent_start", {
                    "agent": node_name,
                    "timestamp": _now(),
                })

                # Merge output into final_state
                final_state.update(node_output)

                # Orchestrator is special — scenario_classified
                if node_name == "orchestrator":
                    scenario = final_state.get("scenario", "poc")
                    emit_event(ticket_id, "scenario_classified", {
                        "scenario": scenario,
                        "path": _path_for_scenario(scenario),
                        "resumed": is_resume,
                    })

                # -- Checkpoint: save after validation_gate, before context_packer
                # The cycle is: agent → validation_gate → context_packer → next agent.
                # Saving here captures the validated agent output with the correct
                # plan index pointing to the NEXT agent to run.  context_packer
                # (which advances the index) will re-run on resume — it's FAST tier.
                if node_name == "validation_gate":
                    # Only save if validation passed (not escalating)
                    if not final_state.get("human_escalation"):
                        save_checkpoint(ticket_id, final_state)

                prev_node = node_name

    except BaseException:
        # -- Emergency checkpoint on any error before propagating -----------
        logger.warning(
            "Pipeline error for %s — saving emergency checkpoint", ticket_id
        )
        try:
            save_checkpoint(ticket_id, final_state)
        except Exception as cp_err:
            logger.error("Failed to save emergency checkpoint: %s", cp_err)
        raise

    # -- last node completion ----------------------------------------------
    if prev_node:
        emit_event(ticket_id, "agent_end", {
            "agent": prev_node,
            "status": final_state.get("status", "unknown"),
            "timestamp": _now(),
        })

    # -- Clean up checkpoint on successful completion -----------------------
    try:
        delete_checkpoint(ticket_id)
    except Exception:
        logger.debug("Failed to delete checkpoint for %s", ticket_id, exc_info=True)

    return final_state
