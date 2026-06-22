"""
PipelineState — the single shared state object passed between all agents
via LangGraph. Every agent reads from and writes to this dict.

All fields use ``Annotated`` with reducers so LangGraph 1.2.5+ can handle
the initial-state write + first-node return without ``InvalidUpdateError``.
"""

import operator
from typing import TypedDict, Optional, List, Literal, Annotated
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Custom reducers for LangGraph channels
# ---------------------------------------------------------------------------

def _keep_latest(_current, update):
    """Last-write-wins for scalar fields (str, int, float, bool)."""
    return update


def _merge_dicts(current, update):
    """Shallow-merge *update* into *current* for dict fields."""
    merged = dict(current or {})
    merged.update(update or {})
    return merged


# ---------------------------------------------------------------------------
# Sub-types
# ---------------------------------------------------------------------------

class AgentMessage(TypedDict):
    from_agent: str
    to_agent: str
    message_type: str
    ticket_id: str
    payload: dict
    timestamp: str


class HumanApproval(TypedDict):
    stage: str
    approved_by: Optional[str]
    status: Literal["pending", "approved", "changes_requested", "rejected"]
    comment: Optional[str]
    at: Optional[str]


# ---------------------------------------------------------------------------
# PipelineState — Annotated for LangGraph 1.2.5+ multi-write tolerance
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    # Identity (scalar — last write wins)
    ticket_id: Annotated[str, _keep_latest]
    scenario: Annotated[str, _keep_latest]
    human_prompt: Annotated[str, _keep_latest]

    # Routing
    current_agent: Annotated[str, _keep_latest]
    next_agent: Annotated[Optional[str], _keep_latest]
    status: Annotated[str, _keep_latest]

    # Agent outputs — dict fields (merge)
    agent_messages: Annotated[List[AgentMessage], operator.add]
    coder_output: Annotated[Optional[dict], _keep_latest]
    design_brief: Annotated[Optional[dict], _keep_latest]
    design_synthesis: Annotated[Optional[dict], _keep_latest]
    hld_output: Annotated[Optional[dict], _keep_latest]
    compliance_report: Annotated[Optional[dict], _keep_latest]
    security_findings: Annotated[Optional[dict], _keep_latest]
    deploy_url: Annotated[Optional[str], _keep_latest]

    # Human in the loop
    human_approval_required: Annotated[bool, _keep_latest]
    human_approvals: Annotated[List[HumanApproval], operator.add]

    # Meta
    created_at: Annotated[str, _keep_latest]
    last_updated: Annotated[str, _keep_latest]
    error: Annotated[Optional[str], _keep_latest]

    # Dynamic planning
    agent_plan: Annotated[List[str], _keep_latest]
    agent_plan_index: Annotated[int, _keep_latest]

    # Structured agent outputs (dict[str, dict] — merge)
    agent_outputs: Annotated[dict, _merge_dicts]
    context_summaries: Annotated[dict, _merge_dicts]
    current_summary: Annotated[Optional[str], _keep_latest]

    # Confidence tracking
    confidence_scores: Annotated[dict, _merge_dicts]
    overall_confidence: Annotated[float, _keep_latest]

    # Iteration tracking
    iteration_count: Annotated[int, _keep_latest]

    # Human escalation
    human_escalation: Annotated[bool, _keep_latest]
    human_escalation_reason: Annotated[Optional[str], _keep_latest]

    # Checkpoint / resume
    resumed_from_checkpoint: Annotated[bool, _keep_latest]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def new_state(ticket_id: str, prompt: str) -> PipelineState:
    """Create a fresh pipeline state for a new ticket."""
    now = datetime.now(timezone.utc).isoformat()
    return PipelineState(
        ticket_id=ticket_id,
        scenario="poc",           # overwritten by orchestrator after classification
        human_prompt=prompt,
        current_agent="orchestrator",
        next_agent=None,
        status="started",
        agent_messages=[],
        coder_output=None,
        design_brief=None,
        design_synthesis=None,
        hld_output=None,
        compliance_report=None,
        security_findings=None,
        deploy_url=None,
        human_approval_required=False,
        human_approvals=[],
        created_at=now,
        last_updated=now,
        error=None,
        # Phase 1 defaults
        agent_plan=[],
        agent_plan_index=0,
        agent_outputs={},
        context_summaries={},
        current_summary=None,
        confidence_scores={},
        overall_confidence=1.0,
        iteration_count=0,
        human_escalation=False,
        human_escalation_reason=None,
        resumed_from_checkpoint=False,
    )


def agent_message(
    from_agent: str,
    to_agent: str,
    message_type: str,
    ticket_id: str,
    payload: dict,
) -> AgentMessage:
    """Helper to build a typed inter-agent message."""
    return AgentMessage(
        from_agent=from_agent,
        to_agent=to_agent,
        message_type=message_type,
        ticket_id=ticket_id,
        payload=payload,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
