"""
Validation Gate — runs after every agent to check output quality.

If confidence is below threshold or status is "failed", routes to human escalation.
During migration, auto-wraps legacy raw-dict agent outputs in AgentOutput.
"""

from datetime import datetime, timezone
from typing import Literal

from core.tracing.langfuse import observe

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
        # ── Check if human already responded to this escalation ───────
        approval = _get_stored_approval(state["ticket_id"], f"escalation_{current}")
        if not approval:
            approval = _get_stored_approval(state["ticket_id"], "compliance_review")
        if not approval:
            # Check for /answer command responses
            approval = _get_stored_approval(state["ticket_id"], "agent_question")
        if not approval:
            # Test affordance: HITL_AUTOPILOT injects a deterministic decision.
            # No-op in production (unset / idle).
            from core.agent_registry.hitl_autopilot import decision as _autopilot
            approval = _autopilot(state["ticket_id"], f"escalation_{current}")

        if approval and approval.get("status"):
            status = approval["status"]

            if status == "approved":
                # Human granted approval — override confidence and continue
                output.status = "ok"
                output.confidence = CONFIDENCE_THRESHOLD_OK
                output.validation_errors = []
                state["human_escalation"] = False
                state["human_escalation_reason"] = None

                # If there's an answer/comment, store it for agents to read
                comment = approval.get("comment", "")
                if comment:
                    answer = {
                        "from_user": approval.get("approved_by", "unknown"),
                        "text": comment,
                        "at": approval.get("at", ""),
                    }
                    answers = state.get("pending_answers", [])
                    answers.append(answer)
                    state["pending_answers"] = answers
                    state["pending_questions"] = []

                slack.status(
                    state["ticket_id"],
                    f"✅ Escalation for *{current}* approved by "
                    f"{approval.get('approved_by', 'human')}"
                    f"{' — answer: ' + comment[:80] if comment else ''}"
                    f" — continuing pipeline."
                )

            elif status == "rejected":
                # Human rejected — permanently fail, don't re-escalate
                output.status = "failed"
                output.validation_errors.insert(0, (
                    f"❌ Compliance review REJECTED by {approval.get('approved_by', 'human')}. "
                    f"Pipeline cannot continue."
                ))
                state["human_escalation"] = True
                state["human_escalation_reason"] = output.validation_errors[0]
                state["status"] = "failed"
                slack.alert(
                    f"🚫 *Pipeline {state['ticket_id']} rejected*\n"
                    f"*Agent:* {current}\n"
                    f"*Rejected by:* {approval.get('approved_by', 'unknown')}\n"
                    f"*Comment:* {approval.get('comment', 'none')}\n"
                    f"The pipeline is permanently blocked. Create a new ticket to restart.",
                    channel="#pipeline-alerts",
                )

            elif status == "changes_requested":
                # Human wants changes — escalate with their feedback
                comment = approval.get("comment", "No details provided.")
                state["human_escalation"] = True
                state["human_escalation_reason"] = (
                    f"Changes requested by {approval.get('approved_by', 'human')}: {comment}"
                )
                slack.status(
                    state["ticket_id"],
                    f"🔄 Changes requested for *{current}* — see Slack thread."
                )

            else:
                # Unknown status — escalate to be safe
                state["human_escalation"] = True
                state["human_escalation_reason"] = (
                    f"Unknown approval status '{status}' — escalating for human review."
                )

        else:
            # No human response yet — escalate and wait
            reason = output.validation_errors[0] if output.validation_errors else (
                f"Agent '{current}' confidence {output.confidence:.0%} is below threshold "
                f"({CONFIDENCE_THRESHOLD_OK:.0%})"
            )
            state["human_escalation"] = True
            state["human_escalation_reason"] = reason

            # Build summary with any pending questions
            summary = (
                f"*Agent:* {current}\n"
                f"*Confidence:* {output.confidence:.0%}\n"
                f"*Status:* {output.status}\n"
                f"*Reason:* {reason}"
            )

            # Include any pending agent questions
            questions = state.get("pending_questions", [])
            if questions:
                q_lines = "\n".join(
                    f"• *{q.get('from_agent', 'agent').title()} asks:* {q.get('question', '?')}"
                    for q in questions
                )
                summary += f"\n\n*Questions:*\n{q_lines}"
                summary += f"\n\n_Respond with:_ `/answer {state['ticket_id']} --resume <your response>`"

            slack.approval_request(
                state["ticket_id"],
                stage=f"escalation_{current}",
                summary=summary,
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


def _get_stored_approval(ticket_id: str, stage: str) -> dict | None:
    """Check if a human already approved this escalation via Slack.

    Approval results are stored in-memory by the Slack interactive handler.
    Returns the approval dict (with ``status`` key), or ``None`` if not found.
    """
    try:
        from core.notifications.slack_commands import get_approval_result
        return get_approval_result(ticket_id, stage)
    except Exception:
        return None
