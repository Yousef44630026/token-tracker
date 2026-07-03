"""RAG span helpers + retrieved-context token-impact. (Phase 11)

A RAG pipeline is a chain of spans: input_preparation -> embedding -> vector_search ->
reranking -> prompt_assembly -> final_generation. These helpers build those spans and record
their annotations on ``Span.metadata`` — they never mint a contributing TokenEvent.

Two deliberate rules:
  - vector_search is measured in NATIVE metrics (num_results, latency), NOT tokens.
  - retrieved-context token counts are ESTIMATES for visibility. The REAL cost is the
    downstream LLM call's input tokens, so the estimate is annotated (with a link to the
    consuming span) but never added to the trace total — that would double count.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from tracker.estimation.local_tokenizer import estimate_tokens
from tracker.models.span import Span

RAG_SPAN_TYPES = (
    "input_preparation",
    "embedding",
    "vector_search",
    "reranking",
    "prompt_assembly",
    "final_generation",
)


def hash_context(text: str) -> str:
    """Content-stable short hash of a retrieved context (for dedup / provenance)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def new_rag_span(
    trace_id: str,
    span_type: str,
    *,
    parent_span_id: str | None = None,
    name: str | None = None,
    span_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Span:
    """Build a RAG span of one of the RAG_SPAN_TYPES (raises ValueError otherwise)."""
    if span_type not in RAG_SPAN_TYPES:
        raise ValueError(f"unknown RAG span_type: {span_type!r}")
    return Span(
        span_id=span_id or f"rag-{uuid.uuid4().hex[:12]}",
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        span_type=span_type,
        name=name or span_type,
        metadata=dict(metadata or {}),
    )


def record_vector_search(span: Span, *, num_results: int, latency_ms: float, **native: Any) -> Span:
    """Annotate a vector_search span with native (non-token) retrieval metrics."""
    span.metadata.update(native)
    span.metadata["num_results"] = num_results
    span.metadata["latency_ms"] = latency_ms
    span.metadata["measured_in_tokens"] = False
    return span


def record_retrieved_context(
    span: Span,
    *,
    context_text: str | None = None,
    estimated_tokens: int | None = None,
    injected_into_prompt: bool,
    downstream_llm_span_id: str | None = None,
) -> Span:
    """Annotate the retrieved context: hash, estimated tokens, injection, downstream link.

    The estimate is for visibility only; it is NEVER summed into the trace total — the real
    cost shows up as the downstream LLM span's input tokens.
    """
    if estimated_tokens is None and context_text is not None:
        estimated_tokens = estimate_tokens(context_text)
    span.metadata["retrieved_context_hash"] = hash_context(context_text) if context_text is not None else None
    span.metadata["retrieved_context_estimated_tokens"] = estimated_tokens or 0
    span.metadata["retrieved_context_injected_into_prompt"] = injected_into_prompt
    span.metadata["downstream_llm_span_id"] = downstream_llm_span_id
    return span
