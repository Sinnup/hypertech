"""
PipelineState — the single shared state object passed between all agents
via LangGraph. Every agent reads from and writes to this dict.
"""

from typing import TypedDict, Optional, List, Literal
from datetime import datetime, timezone


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


class PipelineState(TypedDict):
    # Identity
    ticket_id: str
    scenario: Literal["poc", "internal", "production"]
    human_prompt: str

    # Routing
    current_agent: str
    next_agent: Optional[str]
    status: str

    # Agent outputs
    agent_messages: List[AgentMessage]
    coder_output: Optional[dict]
    design_brief: Optional[dict]
    hld_output: Optional[dict]
    compliance_report: Optional[dict]
    security_findings: Optional[dict]
    deploy_url: Optional[str]

    # Human in the loop
    human_approval_required: bool
    human_approvals: List[HumanApproval]

    # Meta
    created_at: str
    last_updated: str
    error: Optional[str]


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
        hld_output=None,
        compliance_report=None,
        security_findings=None,
        deploy_url=None,
        human_approval_required=False,
        human_approvals=[],
        created_at=now,
        last_updated=now,
        error=None,
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
