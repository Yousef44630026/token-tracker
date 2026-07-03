"""Extra — RAG/agent span helpers, full coverage (Phase 11).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_rag_spans_all.py

Builds each RAG span type, records context by explicit estimate vs text (hash present only
with text), checks agent-span defaults, and the tool-result annotation by explicit estimate.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.workflows.agent_tracker import new_agent_span, new_tool_span, record_tool_result  # noqa: E402
from tracker.workflows.rag_tracker import (  # noqa: E402
    RAG_SPAN_TYPES,
    new_rag_span,
    record_retrieved_context,
    record_vector_search,
)

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# --- every RAG span type builds, with identity + parent ---
for st in RAG_SPAN_TYPES:
    s = new_rag_span("t-1", st, parent_span_id="root", name=f"step:{st}")
    check(s.span_type == st and s.trace_id == "t-1" and s.parent_span_id == "root", f"rag span {st} built")
    check(s.span_id.startswith("rag-") or s.span_id, f"rag span {st} has an id")

# --- retrieved context by EXPLICIT estimate (no text -> hash None) ---
s1 = new_rag_span("t-1", "prompt_assembly")
record_retrieved_context(s1, estimated_tokens=512, injected_into_prompt=True, downstream_llm_span_id="llm-1")
check(s1.metadata["retrieved_context_estimated_tokens"] == 512, "explicit estimate used as-is")
check(s1.metadata["retrieved_context_hash"] is None, "no text -> hash None")
check(s1.metadata["downstream_llm_span_id"] == "llm-1", "downstream link recorded")

# --- retrieved context by TEXT (hash present, estimate derived) ---
s2 = new_rag_span("t-1", "prompt_assembly")
record_retrieved_context(s2, context_text="retrieved chunk " * 10, injected_into_prompt=False)
check(isinstance(s2.metadata["retrieved_context_hash"], str), "text -> hash present")
check(s2.metadata["retrieved_context_estimated_tokens"] > 0, "text -> estimate derived")
check(s2.metadata["retrieved_context_injected_into_prompt"] is False, "injection flag False recorded")

# --- vector_search native metrics ---
vs = new_rag_span("t-1", "vector_search")
record_vector_search(vs, num_results=5, latency_ms=3.2, index="pgvector", top_k=5)
check(vs.metadata["num_results"] == 5 and vs.metadata["index"] == "pgvector", "native metrics + extras stored")
check(vs.metadata["measured_in_tokens"] is False, "vector_search is not token-measured")

# --- agent span defaults ---
a = new_agent_span("t-1", agent_run_id="run", step_index=0, step_type="plan")
m = a.metadata
check(a.span_type == "agent_step", "agent span type")
check(m["loop_count"] == 0 and m["retry_count"] == 0, "agent counters default to 0")
check(m["max_steps_reached"] is False, "max_steps_reached defaults False")
check(m["memory_read_count"] == 0 and m["memory_write_count"] == 0, "memory counters default 0")
check(m["parent_agent_id"] is None and m["sub_agent_id"] is None, "agent links default None")

# --- tool span + explicit tool-result estimate ---
tool = new_tool_span("t-1", tool_name="calculator", tool_call_id="c-9")
record_tool_result(tool, estimated_tokens=42, injected_into_context=True, next_llm_span_id="llm-2")
check(tool.span_type == "tool" and tool.metadata["tool_name"] == "calculator", "tool span built")
check(tool.metadata["tool_result_estimated_tokens"] == 42, "explicit tool-result estimate used")
check(tool.metadata["next_llm_span_id"] == "llm-2", "next_llm_span_id recorded")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
