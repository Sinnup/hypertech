"""
Validation Gate — runs after every agent to check output quality.

If confidence is below threshold or status is "failed", routes to human escalation.
During migration, auto-wraps legacy raw-dict agent outputs in AgentOutput.
"""

from datetime import datetime, timezone
from typing import Literal

from langfuse import observe

from core.state.pipeline_state import PipelineState, agent_message
from core.agent_registry.models import AgentOutput, CONFIDENCE_THRESHOLD_OK
from core.notifications import slack


@observe(name="validation-gate")
def validation_gate_node(state: PipelineState) -> PipelineState:
    """Run after an agent completes. Validate its output and set routing flags."""
    current = state["current_agent"]

    # Check if the agent already produced a structured output
    raw = state.get("agent_outputs", {}).get(current)
    if raw is None:
        # Migration path: agent didn't produce AgentOutput — check legacy fields
        raw = _infer_output(state, current)
        if raw is None:
            # No output at all — treat as ok (infra/utility agents)
            raw = AgentOutput(
                status="ok",
                data={},
                confidence=1.0,
                agent_name=current,
            )
        state["agent_outputs"][current] = raw.model_dump() if isinstance(raw, AgentOutput) else raw

    # Normalize to AgentOutput
    if isinstance(raw, dict):
        output = AgentOutput(**raw)
    else:
        output = raw

    # Store confidence
    state["confidence_scores"][current] = output.confidence

    # Compute rolling overall confidence
    scores = [v for v in state["confidence_scores"].values() if v > 0]
    if scores:
        state["overall_confidence"] = sum(scores) / len(scores)

    # Decide: route to human or continue
    needs_escalation = (
        output.status == "failed"
        or output.confidence < CONFIDENCE_THRESHOLD_OK
    )

    if needs_escalation:
        reason = output.validation_errors[0] if output.validation_errors else (
            f"Agent '{current}' confidence {output.confidence:.0%} is below threshold "
            f"({CONFIDENCE_THRESHOLD_OK:.0%})"
        )
        state["human_escalation"] = True
        state["human_escalation_reason"] = reason

        slack.approval_request(
            state["ticket_id"],
            stage=f"escalation_{current}",
            summary=(
                f"*Agent:* {current}\n"
                f"*Confidence:* {output.confidence:.0%}\n"
                f"*Status:* {output.status}\n"
                f"*Reason:* {reason}"
            ),
        )
    else:
        state["human_escalation"] = False
        state["human_escalation_reason"] = None

    state["agent_outputs"][current] = output.model_dump()
    state["last_updated"] = datetime.now(timezone.utc).isoformat()

    return state


def route_from_gate(state: PipelineState) -> Literal["context_packer", "human_escalation"]:
    """Conditional edge: where does the gate send control next?"""
    if state.get("human_escalation"):
        return "human_escalation"
    return "context_packer"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _infer_output(state: PipelineState, agent_name: str) -> AgentOutput | None:
    """Try to build an AgentOutput from legacy state fields.

    During migration, agents still write to ad-hoc fields like
    ``coder_output``, ``compliance_report``, etc.  This function
    extracts whatever it can find so the validation gate has something
    to work with.
    """
    legacy_map = {
        "coder": "coder_output",
        "ba_compliance": "compliance_report",
        "ux_ui": "design_brief",
        "architect": "hld_output",
        "security": "security_findings",
    }

    field = legacy_map.get(agent_name)
    if field:
        data = state.get(field)
        if data:
            return AgentOutput(
                status="ok",
                data=data,
                confidence=1.0,  # legacy agents haven't been calibrated yet
                agent_name=agent_name,
            )

    return None
