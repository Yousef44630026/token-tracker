"""Async-safe trace/span propagation and cross-service identity."""

from tracker.context.model import TraceContext, new_trace
from tracker.context.threads import ContextPropagatingExecutor, carry_context

__all__ = ["ContextPropagatingExecutor", "TraceContext", "carry_context", "new_trace"]
