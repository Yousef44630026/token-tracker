"""Derived agent workflow metrics."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tracker.analytics._common import (
    authoritative_events,
    is_non_negative_number,
    ratio,
    round_metric,
    span_duration_ms,
)
from tracker.models.span import Span
from tracker.models.trace import Trace

_FAILURE_STATUSES = {"failed", "error", "timeout", "timed_out"}


def _metadata_int(span: Span, key: str) -> int:
    value = span.metadata.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _is_failed_span(span: Span) -> bool:
    status = span.metadata.get("status")
    return status in _FAILURE_STATUSES or span.metadata.get("error") is not None


def build_agent_summary(trace: Trace) -> dict[str, Any]:
    """Return derived metrics for agent steps, tool calls, retries, and loops."""
    agent_spans = [span for span in trace.spans if span.span_type == "agent_step"]
    tool_spans = [span for span in trace.spans if span.span_type == "tool"]
    agent_run_ids = {value for span in agent_spans if isinstance((value := span.metadata.get("agent_run_id")), str) and value}
    spans_by_run: dict[str, list[Span]] = defaultdict(list)
    for span in agent_spans:
        run_id = span.metadata.get("agent_run_id")
        if isinstance(run_id, str) and run_id:
            spans_by_run[run_id].append(span)

    failed_tool_count = sum(1 for span in tool_spans if _is_failed_span(span))
    tool_latencies = [value for span in tool_spans if (value := span_duration_ms(span)) is not None]
    tool_result_tokens = sum(
        int(value) for span in tool_spans if is_non_negative_number(value := span.metadata.get("tool_result_estimated_tokens"))
    )
    injected_tool_result_tokens = sum(
        int(value)
        for span in tool_spans
        if span.metadata.get("tool_result_injected_into_context") is True
        and is_non_negative_number(value := span.metadata.get("tool_result_estimated_tokens"))
    )
    step_retry_count = sum(_metadata_int(span, "retry_count") for span in agent_spans)
    memory_reads = sum(_metadata_int(span, "memory_read_count") for span in agent_spans)
    memory_writes = sum(_metadata_int(span, "memory_write_count") for span in agent_spans)
    max_loop_count = max((_metadata_int(span, "loop_count") for span in agent_spans), default=0)
    max_steps_reached_count = sum(1 for span in agent_spans if span.metadata.get("max_steps_reached") is True)
    sub_agents = {value for span in agent_spans if isinstance((value := span.metadata.get("sub_agent_id")), str) and value}

    events = authoritative_events(trace)
    agent_span_ids = {span.span_id for span in agent_spans + tool_spans}
    agent_tokens = sum(event.event_contributing_tokens for event in events if event.span_id in agent_span_ids)

    # Per-run token totals, for the "per successful run" average below. Built from agent_step
    # spans ONLY: tool spans carry no agent_run_id (new_tool_span has no such parameter), so a
    # tool call's tokens cannot be reliably attributed to one specific run — they stay inside
    # the trace-wide `agent_tokens` headline above, but are excluded from this per-run split
    # rather than being silently misattributed to whichever run happens to be counted.
    tokens_by_run: dict[str, int] = {}
    successful_run_ids: set[str] = set()
    for run_id, spans in spans_by_run.items():
        run_span_ids = {span.span_id for span in spans}
        tokens_by_run[run_id] = sum(event.event_contributing_tokens for event in events if event.span_id in run_span_ids)
        if not any(_is_failed_span(span) or span.metadata.get("max_steps_reached") is True for span in spans):
            successful_run_ids.add(run_id)
    successful_runs = len(successful_run_ids)

    tokens_per_run: float | None = agent_tokens / len(agent_run_ids) if agent_run_ids else None
    # NUMERATOR is tokens from successful runs ONLY (not agent_tokens, which includes failed
    # runs too) — using agent_tokens here was the bug: a single expensive FAILED run would
    # silently inflate the average cost reported for a SUCCESSFUL one.
    successful_run_tokens = sum(tokens_by_run[run_id] for run_id in successful_run_ids)
    tokens_per_successful_run: float | None = successful_run_tokens / successful_runs if successful_runs else None

    return {
        "agent_run_count": len(agent_run_ids),
        "successful_agent_run_count": successful_runs,
        "agent_step_count": len(agent_spans),
        "tool_call_count": len(tool_spans),
        "failed_tool_call_count": failed_tool_count,
        "tool_failure_rate": ratio(failed_tool_count, len(tool_spans)),
        "average_tool_latency_ms": round_metric(
            sum(tool_latencies) / len(tool_latencies) if tool_latencies else None,
            3,
        ),
        "retry_count": step_retry_count,
        "max_loop_count": max_loop_count,
        "max_steps_reached_count": max_steps_reached_count,
        "memory_read_count": memory_reads,
        "memory_write_count": memory_writes,
        "sub_agent_count": len(sub_agents),
        "tool_result_estimated_tokens": tool_result_tokens,
        "injected_tool_result_estimated_tokens": injected_tool_result_tokens,
        "agent_contributing_tokens": agent_tokens,
        "tokens_per_agent_run": round_metric(tokens_per_run, 3),
        "tokens_per_successful_agent_run": round_metric(tokens_per_successful_run, 3),
    }


__all__ = ["build_agent_summary"]
