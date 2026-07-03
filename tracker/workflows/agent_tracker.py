"""Agent span helpers + tool-result token-impact. (Phase 11)

Helpers to build agent-step and tool-call spans and record their metadata on
``Span.metadata``. Like the RAG helpers, they never mint a contributing TokenEvent.

A tool result's token count is an ESTIMATE for visibility — the real cost is the NEXT LLM
call's input tokens (the tool output becomes part of that prompt). So the estimate is
annotated and linked to the consuming span, but never added to the trace total.
"""

from __future__ import annotations

import uuid
from typing import Any

from tracker.estimation.local_tokenizer import estimate_tokens
from tracker.models.span import Span


def new_agent_span(
    trace_id: str,
    *,
    agent_run_id: str,
    step_index: int,
    step_type: str,
    parent_span_id: str | None = None,
    parent_agent_id: str | None = None,
    sub_agent_id: str | None = None,
    loop_count: int = 0,
    max_steps_reached: bool = False,
    retry_count: int = 0,
    memory_read_count: int = 0,
    memory_write_count: int = 0,
    span_id: str | None = None,
    name: str | None = None,
) -> Span:
    """Build an agent-step span carrying the full agent run/step metadata."""
    return Span(
        span_id=span_id or f"agent-{uuid.uuid4().hex[:12]}",
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        span_type="agent_step",
        name=name or step_type,
        metadata={
            "agent_run_id": agent_run_id,
            "step_index": step_index,
            "step_type": step_type,
            "parent_agent_id": parent_agent_id,
            "sub_agent_id": sub_agent_id,
            "loop_count": loop_count,
            "max_steps_reached": max_steps_reached,
            "retry_count": retry_count,
            "memory_read_count": memory_read_count,
            "memory_write_count": memory_write_count,
        },
    )


def new_tool_span(
    trace_id: str,
    *,
    tool_name: str,
    tool_call_id: str,
    parent_span_id: str | None = None,
    span_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Span:
    """Build a tool-call span identified by tool_name + tool_call_id."""
    md = dict(metadata or {})
    md["tool_name"] = tool_name
    md["tool_call_id"] = tool_call_id
    return Span(
        span_id=span_id or f"tool-{uuid.uuid4().hex[:12]}",
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        span_type="tool",
        name=tool_name,
        metadata=md,
    )


def record_tool_result(
    span: Span,
    *,
    result_text: str | None = None,
    estimated_tokens: int | None = None,
    injected_into_context: bool,
    next_llm_span_id: str | None = None,
) -> Span:
    """Annotate a tool result's token impact (estimate only; never summed into the total)."""
    if estimated_tokens is None and result_text is not None:
        estimated_tokens = estimate_tokens(result_text)
    span.metadata["tool_result_estimated_tokens"] = estimated_tokens or 0
    span.metadata["tool_result_injected_into_context"] = injected_into_context
    span.metadata["next_llm_span_id"] = next_llm_span_id
    return span
