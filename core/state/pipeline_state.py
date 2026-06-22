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

    # Agent outputs (legacy — kept for backward compatibility)
    agent_messages: List[AgentMessage]
    coder_output: Optional[dict]
    design_brief: Optional[dict]
    design_synthesis: Optional[dict]   # Phase 2: merged output from design_synthesizer
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

    # === Phase 1 new fields ===

    # Dynamic planning
    agent_plan: List[str]           # ordered list of agent names to execute
    agent_plan_index: int           # current position in the plan

    # Structured agent outputs (AgentOutput stored as plain dict for TypedDict compat)
    agent_outputs: dict            # dict[str, dict] — keyed by agent name
    context_summaries: dict         # dict[str, str] — keyed by agent name
    current_summary: Optional[str]  # active summary for the next agent to consume

    # Confidence tracking
    confidence_scores: dict         # dict[str, float] — per-agent confidence
    overall_confidence: float       # rolling pipeline confidence (product of all)

    # Iteration tracking (for review loops)
    iteration_count: int

    # Human escalation
    human_escalation: bool
    human_escalation_reason: Optional[str]

    # Checkpoint / resume
    resumed_from_checkpoint: bool


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
