"""Regression (A1) — agent token attribution must include DESCENDANT spans' events.

Real agent traces nest: an agent_step span has an LLM-call child span (created by the
propagation layer), and the TokenEvent attaches to that child. build_agent_summary counted
only events whose span_id was an agent_step/tool span id — nested LLM calls' tokens were
missing from agent_contributing_tokens and from every per-run figure, understating agent
cost exactly where nesting is deepest. Attribution must follow the span tree: an event
belongs to an agent step if its span IS the step or is a descendant of it.

Run: python tests/test_agent_descendant_attribution.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.agent import build_agent_summary  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def llm_event(eid, span_id, tokens):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=f"rc-{eid}",
        trace_id="t",
        span_id=span_id,
        quantities=[
            TokenQuantity(TokenType.INPUT, tokens, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        observation={"authoritative": True},
    )


trace = Trace(trace_id="t")

# agent run with one step; the LLM call lives in a CHILD span of the step
step = new_agent_span("t", agent_run_id="run-1", step_index=0, step_type="reasoning", span_id="step-1")
trace.add_span(step)
llm_span = Span(span_id="llm-1", trace_id="t", parent_span_id="step-1", span_type="llm", name="call")
trace.add_span(llm_span)
# and a grandchild (retry sub-span under the llm span)
retry_span = Span(span_id="llm-1-retry", trace_id="t", parent_span_id="llm-1", span_type="llm", name="retry")
trace.add_span(retry_span)

trace.add_event(llm_event("e-direct", "step-1", 100))  # directly on the step
trace.add_event(llm_event("e-child", "llm-1", 500))  # on the child llm span
trace.add_event(llm_event("e-grandchild", "llm-1-retry", 250))  # on the grandchild

# an unrelated span + event outside the agent: must NOT be attributed
outside = Span(span_id="other", trace_id="t", parent_span_id=None, span_type="llm", name="other")
trace.add_span(outside)
trace.add_event(llm_event("e-outside", "other", 999))

summary = build_agent_summary(trace)

check(
    summary["agent_contributing_tokens"] == 850,
    f"agent tokens include child+grandchild spans: 100+500+250=850 (got {summary['agent_contributing_tokens']})",
)
check(summary["agent_run_count"] == 1, "one agent run")
check(
    summary["tokens_per_agent_run"] == 850.0,
    f"per-run figure also follows the span tree (got {summary['tokens_per_agent_run']})",
)
check(
    summary["tokens_per_successful_agent_run"] == 850.0,
    f"successful-run figure too (got {summary['tokens_per_successful_agent_run']})",
)

# outside event untouched
check(summary["agent_contributing_tokens"] != 850 + 999, "the unrelated span's 999 tokens are NOT attributed to the agent")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
