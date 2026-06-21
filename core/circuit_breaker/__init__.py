"""
Circuit Breaker — decorator for external service calls with timeout, fallback,
and Slack approval gating.

    circuit_breaker     — decorator factory
    CircuitBreakerError — raised when all paths fail or fallback is rejected
    CircuitBreakerResult— structured result from a wrapped call
"""

from core.circuit_breaker.decorator import circuit_breaker, CircuitBreakerError
from core.circuit_breaker.models import CircuitBreakerResult
