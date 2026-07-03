"""Derived RAG workflow metrics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tracker.analytics._common import (
    authoritative_events,
    event_input_tokens,
    is_non_negative_number,
    ratio,
    round_metric,
    span_duration_ms,
)
from tracker.models.trace import Trace


def build_rag_summary(trace: Trace) -> dict[str, Any]:
    """Return retrieval and prompt-assembly efficiency metrics.

    ``retrieved_not_injected_ratio`` is deliberately NOT called "waste": this project's own RAG
    span model includes a ``reranking`` stage whose entire job is to retrieve more candidates
    than get injected. A high ratio here is very often reranking working as designed, not a
    defect — the field name no longer presupposes which one it is.
    """
    spans = [
        span
        for span in trace.spans
        if span.span_type
        in {
            "input_preparation",
            "embedding",
            "vector_search",
            "reranking",
            "prompt_assembly",
            "final_generation",
        }
    ]
    span_counts = Counter(span.span_type or "unknown" for span in spans)
    vector_spans = [span for span in spans if span.span_type == "vector_search"]
    vector_latencies = [value for span in vector_spans if (value := span_duration_ms(span)) is not None]
    vector_results = sum(
        value
        for span in vector_spans
        if isinstance((value := span.metadata.get("num_results")), int) and not isinstance(value, bool) and value >= 0
    )

    retrieved_tokens = 0
    injected_tokens = 0
    downstream_span_ids: set[str] = set()
    context_hashes: list[str] = []
    for span in spans:
        estimated = span.metadata.get("retrieved_context_estimated_tokens")
        if is_non_negative_number(estimated):
            retrieved_tokens += int(estimated)
            if span.metadata.get("retrieved_context_injected_into_prompt") is True:
                injected_tokens += int(estimated)
            downstream = span.metadata.get("downstream_llm_span_id")
            if isinstance(downstream, str) and downstream:
                downstream_span_ids.add(downstream)
        context_hash = span.metadata.get("retrieved_context_hash")
        if isinstance(context_hash, str) and context_hash:
            context_hashes.append(context_hash)

    events = authoritative_events(trace)
    if downstream_span_ids:
        downstream_input_tokens = sum(event_input_tokens(event) for event in events if event.span_id in downstream_span_ids)
    else:
        final_generation_ids = {span.span_id for span in spans if span.span_type == "final_generation"}
        downstream_input_tokens = sum(event_input_tokens(event) for event in events if event.span_id in final_generation_ids)

    return {
        "rag_span_count": len(spans),
        "span_counts": dict(sorted(span_counts.items())),
        "vector_search_count": len(vector_spans),
        "vector_search_results": vector_results,
        "average_vector_search_latency_ms": round_metric(
            sum(vector_latencies) / len(vector_latencies) if vector_latencies else None,
            3,
        ),
        "retrieved_context_tokens": retrieved_tokens,
        "injected_context_tokens": injected_tokens,
        "retrieved_not_injected_tokens": max(retrieved_tokens - injected_tokens, 0),
        "downstream_llm_input_tokens": downstream_input_tokens,
        "context_utilization_ratio": ratio(injected_tokens, downstream_input_tokens),
        "retrieved_not_injected_ratio": ratio(max(retrieved_tokens - injected_tokens, 0), retrieved_tokens),
        "retrieved_context_hash_count": len(context_hashes),
        # a repeated hash across turns is often a stable/relevant document retrieved again,
        # not necessarily a problem — reported as a plain count, no "duplicate is bad" framing.
        "repeated_context_hash_count": len(context_hashes) - len(set(context_hashes)),
    }


__all__ = ["build_rag_summary"]
