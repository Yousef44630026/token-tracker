"""DEEP agent workflow x AWS Bedrock — a realistic multi-turn agent loop (tool calls,
loop_count, step tracking) driven by the REAL Bedrock Converse adapter, extending Scenario B
(which used Anthropic) to the AWS provider we just validated against a real payload.

Run: python tests/test_agent_bedrock_deep.py

Bedrock-specific realities this test respects (all previously confirmed):
  - cache fields (cacheReadInputTokens/cacheWriteInputTokens) are additive input buckets;
    AWS documents inputTokens as only the non-cached portion when caching is enabled.
  - the response body never echoes the model name (confirmed against a real capture) — model
    stays None throughout, which must not be mistaken for a bug.
  - no total field ambiguity: Converse DOES report totalTokens, unlike Anthropic/Cohere/Voyage
    InvokeModel — every turn must reconcile exactly.

Three parts: a realistic 4-step agent run (plan -> 2 tool calls -> final answer), a
max-steps-reached variant, and a randomized fuzz across many agent-loop shapes.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.context.propagation import new_trace, span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span, new_tool_span, record_tool_result  # noqa: E402

_failures = 0
_checks = 0


def check(cond, msg):
    global _failures, _checks
    _checks += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


def bedrock_turn(input_t, output_t, cache_read=0, cache_write=0):
    """A Converse response payload with the REAL, confirmed shape (no modelId echoed back)."""
    return {
        "usage": {
            "inputTokens": input_t,
            "outputTokens": output_t,
            "totalTokens": input_t + cache_read + cache_write + output_t,
            "cacheReadInputTokens": cache_read,
            "cacheWriteInputTokens": cache_write,
        }
    }


# =====================================================================================
# PART 1 — a realistic 4-step agent run over Bedrock Converse
# =====================================================================================
print("--- Part 1: realistic agent loop (plan -> 2 tool calls -> final answer) via Bedrock ---")

ORDER_LOOKUP_RESULT = (
    '{"order_id": "NW-772103", "status": "shipped", "carrier": "UPS", '
    '"tracking": "1Z999AA10123456784", "estimated_delivery": "2026-07-05"}'
)
CARRIER_STATUS_RESULT = (
    '{"tracking": "1Z999AA10123456784", "last_scan": "2026-07-01T08:14:00Z", '
    '"location": "Regional facility, Lyon FR", "status": "In transit"}'
)

adapter = BedrockConverseAdapter()
agent_run_id = "run-bedrock-agent-1"
# (non-cached input, output, cache_read, cache_write) per turn
turns = [(320, 45, 0, 0), (80, 60, 400, 0), (110, 55, 500, 0), (140, 180, 550, 50)]
expected_total = sum(i + o + cache_read + cache_write for i, o, cache_read, cache_write in turns)

with trace(business_id="northwind", workflow="bedrock_agent") as root:
    tr = Trace(trace_id=root.trace_id, business_id="northwind", workflow="bedrock_agent")

    with span() as s0:
        step0 = new_agent_span(
            tr.trace_id,
            agent_run_id=agent_run_id,
            step_index=0,
            step_type="plan",
            parent_span_id=root.span_id,
            span_id=s0.span_id,
            loop_count=0,
        )
        tr.add_span(step0)
        ev0 = normalize(bedrock_turn(*turns[0]), adapter, context=s0)
        tr.add_event(ev0)
    check(ev0.model is None, "turn 0: Bedrock Converse reports no model in body (confirmed real behavior, not a bug)")
    check(ev0.event_total_mismatch == 0, "turn 0: reconciles")
    check(
        q(ev0, TokenType.CACHED_INPUT).quantity_in_total == 0 if q(ev0, TokenType.CACHED_INPUT) else True,
        "turn 0: zero cache usage creates no additive quantity",
    )

    with span() as tool1_ctx:
        tool1 = new_tool_span(
            tr.trace_id, tool_name="lookup_order", tool_call_id="call-1", parent_span_id=root.span_id, span_id=tool1_ctx.span_id
        )
        record_tool_result(tool1, result_text=ORDER_LOOKUP_RESULT, injected_into_context=True)
        tr.add_span(tool1)

    with span() as s1:
        step1 = new_agent_span(
            tr.trace_id,
            agent_run_id=agent_run_id,
            step_index=1,
            step_type="tool_call",
            parent_span_id=root.span_id,
            span_id=s1.span_id,
            loop_count=1,
        )
        tr.add_span(step1)
        ev1 = normalize(bedrock_turn(*turns[1]), adapter, context=s1)
        tr.add_event(ev1)
    check(ev1.event_total_mismatch == 0, "turn 1: reconciles")

    with span() as tool2_ctx:
        tool2 = new_tool_span(
            tr.trace_id, tool_name="carrier_status", tool_call_id="call-2", parent_span_id=root.span_id, span_id=tool2_ctx.span_id
        )
        record_tool_result(tool2, result_text=CARRIER_STATUS_RESULT, injected_into_context=True)
        tr.add_span(tool2)

    with span() as s2:
        step2 = new_agent_span(
            tr.trace_id,
            agent_run_id=agent_run_id,
            step_index=2,
            step_type="tool_call",
            parent_span_id=root.span_id,
            span_id=s2.span_id,
            loop_count=2,
        )
        tr.add_span(step2)
        ev2 = normalize(bedrock_turn(*turns[2]), adapter, context=s2)
        tr.add_event(ev2)
    check(ev2.event_total_mismatch == 0, "turn 2: reconciles")

    with span() as s3:
        step3 = new_agent_span(
            tr.trace_id,
            agent_run_id=agent_run_id,
            step_index=3,
            step_type="final_answer",
            parent_span_id=root.span_id,
            span_id=s3.span_id,
            loop_count=3,
            max_steps_reached=False,
        )
        tr.add_span(step3)
        ev3 = normalize(bedrock_turn(*turns[3]), adapter, context=s3)
        tr.add_event(ev3)
    check(ev3.event_total_mismatch == 0, "turn 3 (final): reconciles")

tool_estimates = [tool1.metadata["tool_result_estimated_tokens"], tool2.metadata["tool_result_estimated_tokens"]]
grand_total = observed_total_contributing_tokens(tr)
check(grand_total == expected_total, f"4-turn Bedrock agent run: total reconciles exactly ({grand_total} != {expected_total})")
check(grand_total != expected_total + sum(tool_estimates), "tool-result estimates never double-counted into the Bedrock agent total")
check(step3.metadata["max_steps_reached"] is False, "agent completed within budget")
check(len(tr.spans) == 6, f"4 agent-step spans + 2 tool spans recorded (got {len(tr.spans)})")

# =====================================================================================
# PART 2 — max-steps-reached variant
# =====================================================================================
print("\n--- Part 2: an agent run that hits the step budget ---")

with trace(business_id="northwind", workflow="bedrock_agent") as root2:
    tr2 = Trace(trace_id=root2.trace_id)
    with span() as sctx:
        maxed = new_agent_span(
            tr2.trace_id,
            agent_run_id="run-bedrock-maxed",
            step_index=15,
            step_type="tool_call",
            parent_span_id=root2.span_id,
            span_id=sctx.span_id,
            loop_count=15,
            max_steps_reached=True,
            retry_count=3,
        )
        tr2.add_span(maxed)
        ev_maxed = normalize(bedrock_turn(900, 20), adapter, context=sctx)
        tr2.add_event(ev_maxed)
check(maxed.metadata["max_steps_reached"] is True and maxed.metadata["loop_count"] == 15, "max-steps-reached agent step correctly recorded")
check(ev_maxed.event_contributing_tokens == 920, "the LLM call that hit the budget still contributes its real cost (920)")

# =====================================================================================
# PART 3 — randomized fuzz across many agent-loop shapes over Bedrock Converse
# =====================================================================================
print("\n--- Part 3: randomized agent-loop shapes over Bedrock Converse ---")

SEED = int(os.environ.get("FUZZ_SEED", "13131313"))
rng = random.Random(SEED)
_uid = 0


def uid(p="u"):
    global _uid
    _uid += 1
    return f"{p}-{_uid}"


N_RUNS = 60
for i in range(N_RUNS):
    n_steps = rng.randint(1, 8)
    run_trace_id = uid("t-bedrock-agent-fuzz")
    tr_f = Trace(trace_id=run_trace_id)
    expected = 0
    for step in range(n_steps):
        inp, out = rng.randint(10, 5000), rng.randint(1, 800)
        # Cache fields are separate additive input buckets under AWS's documented formula.
        cache_read = rng.randint(0, 3000) if rng.random() < 0.4 else 0
        cache_write = rng.randint(0, 500) if rng.random() < 0.2 else 0
        ctx = new_trace(trace_id=run_trace_id)
        ev = normalize(bedrock_turn(inp, out, cache_read, cache_write), adapter, context=ctx)
        tr_f.add_event(ev)
        expected += inp + out + cache_read + cache_write
        check(ev.event_total_mismatch == 0, f"fuzz run #{i} step {step}: reconciles")
    got = observed_total_contributing_tokens(tr_f)
    check(got == expected, f"fuzz run #{i} ({n_steps} steps): agent-loop total matches ({got} != {expected})")

print(f"[INFO] Part 3: {N_RUNS} randomized agent-loop runs over Bedrock Converse (seed={SEED}).")

print(f"\n[INFO] total checks run: {_checks}")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
