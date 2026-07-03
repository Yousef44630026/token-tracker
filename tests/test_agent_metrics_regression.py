"""Regression — tokens_per_successful_agent_run must not absorb failed runs' cost.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_agent_metrics_regression.py

Found during a rigorous logic/relevance review of tracker/analytics/agent.py: the numerator
(``agent_tokens``, ALL runs' tokens) and the denominator (``successful_runs``, only successful
run COUNT) covered different populations. A single expensive FAILED run silently inflated the
reported "cost of a successful run" because its tokens stayed in the numerator while its run
was excluded from the denominator. Fixed by building per-run token totals and summing only the
SUCCESSFUL runs' tokens into the numerator.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.analytics.agent import build_agent_summary  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def openai_event(prompt_t, completion_t, *, trace_id, span_id):
    payload = {
        "model": "gpt-4o",
        "usage": {"prompt_tokens": prompt_t, "completion_tokens": completion_t, "total_tokens": prompt_t + completion_t},
    }
    ctx = TraceContext(trace_id=trace_id, span_id=span_id, request_correlation_id=uuid.uuid4().hex)
    return normalize(payload, OpenAIChatCompletionsAdapter(), context=ctx)


# --- the exact adversarial scenario from the review: 1 cheap successful run, 1 EXPENSIVE
# failed run. The old formula reported (100 + 100_000) / 1 == 100_100 as "cost of a
# successful run" — wildly overstating it by absorbing the failed run's cost.
trace_id = "agent-metrics-regression"
tr = Trace(trace_id=trace_id)

cheap_span = new_agent_span(trace_id, agent_run_id="run-cheap-success", step_index=0, step_type="final_answer", span_id="span-cheap")
tr.add_span(cheap_span)
tr.add_event(openai_event(80, 20, trace_id=trace_id, span_id="span-cheap"))  # 100 tokens, successful run

expensive_span = new_agent_span(trace_id, agent_run_id="run-expensive-fail", step_index=0, step_type="tool_call", span_id="span-expensive")
expensive_span.metadata["status"] = "failed"
tr.add_span(expensive_span)
tr.add_event(openai_event(90000, 10000, trace_id=trace_id, span_id="span-expensive"))  # 100,000 tokens, FAILED run

summary = build_agent_summary(tr)
check(summary["agent_run_count"] == 2, "both runs counted")
check(summary["successful_agent_run_count"] == 1, "only the cheap run is successful")
check(summary["agent_contributing_tokens"] == 100_100, "trace-wide headline total still includes BOTH runs (100 + 100,000)")
check(
    summary["tokens_per_successful_agent_run"] == 100.0,
    f"FIXED: cost of a successful run reflects ONLY the successful run's 100 tokens, "
    f"not the failed run's 100,000 (got {summary['tokens_per_successful_agent_run']})",
)
check(
    summary["tokens_per_successful_agent_run"] != 100_100.0,
    "the old bug (100,100) must not reappear",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
