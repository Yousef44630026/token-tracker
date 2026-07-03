"""Async-safe trace/span propagation and cross-service identity."""

from tracker.context.model import TraceContext, new_trace

__all__ = ["TraceContext", "new_trace"]
