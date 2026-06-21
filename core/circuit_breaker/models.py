"""
Circuit breaker result types.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class CircuitBreakerResult(BaseModel):
    """Result of a circuit-breaker-wrapped call."""

    service: str
    status: Literal["primary_ok", "fallback_used", "failed", "rejected"] = "primary_ok"
    primary_error: Optional[str] = None
    fallback_error: Optional[str] = None
    approval_requested: bool = False
    approval_granted: Optional[bool] = None
    latency_ms: float = 0.0
