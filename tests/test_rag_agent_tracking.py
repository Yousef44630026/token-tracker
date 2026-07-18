"""Phase 11 — RAG + agent span helpers and tool-result token-impact (INV-2/INV-3/INV-6).

Run: python tests/test_rag_agent_tracking.py

RAG and agent helpers annotate SPANS (metadata), they never mint contributing TokenEvents.
Retrieved-context and tool-result token counts are ESTIMATES for visibility — the real cost
is the downstream LLM call's input tokens, so the estimates must NOT be added to the trace
total (that would double count). vector_search is measured in native metrics, not tokens.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span, new_tool_span, record_tool_result  # noqa: E402
from tracker.workflows.rag_tracker import (  # noqa: E402
    RAG_SPAN_TYPES,
    new_rag_span,
    record_retrieved_context,
    record_vector_search,
)

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


# --- RAG span types ---
expected = {"input_preparation", "embedding", "vector_search", "reranking", "prompt_assembly", "final_generation"}
check(set(RAG_SPAN_TYPES) == expected, "the six RAG span types are defined")
for st in RAG_SPAN_TYPES:
    s = new_rag_span("t-1", st, parent_span_id="root")
    check(s.span_type == st and s.trace_id == "t-1", f"new_rag_span builds a '{st}' span")

bad = False
try:
    new_rag_span("t-1", "not_a_rag_phase")
except ValueError:
    bad = True
check(bad, "an unknown RAG span_type is rejected")

# --- vector_search: native metrics, NOT tokens ---
vs = new_rag_span("t-1", "vector_search", parent_span_id="root")
record_vector_search(vs, num_results=8, latency_ms=12.5, index="faiss")
check(vs.metadata["num_results"] == 8 and vs.metadata["latency_ms"] == 12.5, "vector_search carries native metrics")
check(vs.metadata.get("measured_in_tokens") is False, "vector_search is explicitly NOT token-measured")

# --- retrieved context: an estimate annotation linked to the downstream LLM ---
pa = new_rag_span("t-1", "prompt_assembly", parent_span_id="root", span_id="span-assembly")
record_retrieved_context(pa, context_text="some retrieved passage " * 20, injected_into_prompt=True, downstream_llm_span_id="span-llm")
md = pa.metadata
check(isinstance(md["retrieved_context_hash"], str) and len(md["retrieved_context_hash"]) > 0, "retrieved_context_hash is set")
check(md["retrieved_context_estimated_tokens"] > 0, "retrieved_context_estimated_tokens is a positive estimate")
check(md["retrieved_context_injected_into_prompt"] is True, "retrieved_context_injected_into_prompt flag is recorded")
check(md["downstream_llm_span_id"] == "span-llm", "downstream_llm_span_id links to the LLM that consumes it")
# the hash is stable for the same content
pa2 = new_rag_span("t-1", "prompt_assembly")
record_retrieved_context(pa2, context_text="some retrieved passage " * 20, injected_into_prompt=True)
check(pa2.metadata["retrieved_context_hash"] == md["retrieved_context_hash"], "retrieved_context_hash is content-stable")

# --- RAG no-double-count: the LLM input is counted, the context estimate is NOT ---
trace = Trace(trace_id="t-1")
trace.add_span(pa)
llm = TokenEvent(
    event_id="evt-llm",
    request_correlation_id="r-llm",
    trace_id="t-1",
    span_id="span-llm",
    quantities=[
        TokenQuantity(
            token_type=TokenType.INPUT,
            quantity=520,
            precision_level=PrecisionLevel.EXACT,
            usage_source=UsageSource.PROVIDER_RESPONSE,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        ),
        TokenQuantity(
            token_type=TokenType.OUTPUT,
            quantity=80,
            precision_level=PrecisionLevel.EXACT,
            usage_source=UsageSource.PROVIDER_RESPONSE,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        ),
    ],
    provider_total_tokens=600,
    observation={"authoritative": True},
)
trace.add_event(llm)
total = observed_total_contributing_tokens(trace)
ctx_est = pa.metadata["retrieved_context_estimated_tokens"]
check(total == 600, f"trace total counts the LLM event only (got {total})")
check(total != 600 + ctx_est, "the retrieved-context estimate is NOT added on top (no double count)")

# --- agent metadata ---
step = new_agent_span(
    "t-1",
    agent_run_id="run-9",
    step_index=3,
    step_type="tool_call",
    parent_span_id="root",
    parent_agent_id="agent-root",
    sub_agent_id="agent-child",
    loop_count=2,
    max_steps_reached=False,
    retry_count=1,
    memory_read_count=4,
    memory_write_count=1,
)
m = step.metadata
for key, val in {
    "agent_run_id": "run-9",
    "step_index": 3,
    "step_type": "tool_call",
    "parent_agent_id": "agent-root",
    "sub_agent_id": "agent-child",
    "loop_count": 2,
    "max_steps_reached": False,
    "retry_count": 1,
    "memory_read_count": 4,
    "memory_write_count": 1,
}.items():
    check(m[key] == val, f"agent span carries {key}={val!r}")

# --- tool-result token-impact: estimate annotation, linked to the next LLM, not double counted ---
tool = new_tool_span("t-1", tool_name="search_web", tool_call_id="call-1", parent_span_id="root")
record_tool_result(tool, result_text="lots of tool output " * 30, injected_into_context=True, next_llm_span_id="span-llm2")
tm = tool.metadata
check(tm["tool_name"] == "search_web" and tm["tool_call_id"] == "call-1", "tool span carries tool_name/tool_call_id")
check(tm["tool_result_estimated_tokens"] > 0, "tool_result_estimated_tokens is a positive estimate")
check(tm["tool_result_injected_into_context"] is True, "tool_result_injected_into_context flag is recorded")
check(tm["next_llm_span_id"] == "span-llm2", "next_llm_span_id links to the LLM that consumes the tool result")

trace2 = Trace(trace_id="t-1")
trace2.add_span(tool)
llm2 = TokenEvent(
    event_id="evt-llm2",
    request_correlation_id="r-llm2",
    trace_id="t-1",
    span_id="span-llm2",
    quantities=[
        TokenQuantity(
            token_type=TokenType.INPUT,
            quantity=420,
            precision_level=PrecisionLevel.EXACT,
            usage_source=UsageSource.PROVIDER_RESPONSE,
            additivity=Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    provider_total_tokens=420,
    observation={"authoritative": True},
)
trace2.add_event(llm2)
total2 = observed_total_contributing_tokens(trace2)
check(total2 == 420, f"tool-result estimate is not summed into the total (got {total2})")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
