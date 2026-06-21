"""
Pydantic models for the agent registry and structured agent outputs.

AgentOutput is the standard envelope every agent returns — it carries
the agent's data, a confidence score, and validation metadata.
"""

from typing import Optional, Literal
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from core.ai.models import ModelTier


# ---------------------------------------------------------------------------
# AgentOutput — the standard envelope for every agent's output
# ---------------------------------------------------------------------------

class AgentOutput(BaseModel):
    """Every agent wraps its output in this structure before returning state."""

    status: Literal["ok", "degraded", "failed"] = "ok"
    data: dict = Field(default_factory=dict)
    validation_errors: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    agent_name: str = ""
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD_OK = 0.6       # below this → route to human escalation
CONFIDENCE_THRESHOLD_DEGRADED = 0.3  # below this → treat as failed


# ---------------------------------------------------------------------------
# AgentDef — metadata about a registered agent
# ---------------------------------------------------------------------------

@dataclass
class AgentDef:
    """Metadata for one registered agent node in the graph."""

    name: str
    description: str = ""
    tier: ModelTier = ModelTier.BALANCED
    tags: list[str] = field(default_factory=list)
    fn: object = None
    visible: bool = True  # False for infrastructure nodes (validation_gate, etc.)
