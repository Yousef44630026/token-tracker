"""DEEP realistic scenario B — a substantial agent run: 3 realistic tool calls (search,
lookup) feeding an Anthropic conversation whose cache grows as tool results accumulate in
context, plus a max-steps-reached variant.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_scenario_b_agent_tool_calls.py

Split out of the original test_realistic_scenarios_deep.py — see test_scenario_a_rag_conversation.py
for the sibling scenarios and the reasoning behind the split.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span, new_tool_span, record_tool_result  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


print("\n--- Scenario B: agent run with 3 tool calls, Anthropic growing cache ---")

TICKET_SEARCH_RESULT = (
    '{"results": [{"id": "TCK-88213", "customer": "Maria Chen", "opened": "2026-06-24", '
    '"subject": "Refund not received after 10 days", "priority": "high"}, '
    '{"id": "TCK-88250", "customer": "David Osei", "opened": "2026-06-26", '
    '"subject": "Wrong item refunded, need correction", "priority": "medium"}, '
    '{"id": "TCK-88310", "customer": "Priya Nair", "opened": "2026-06-27", '
    '"subject": "Refund amount incorrect by $12.40", "priority": "medium"}]}'
)

TICKET_DETAIL_RESULT = (
    '{"id": "TCK-88213", "customer": "Maria Chen", "order_number": "NW-559214", '
    '"requested_amount": "$142.50", "history": "Customer returned item on 2026-06-14, '
    "warehouse confirmed receipt 2026-06-17, refund still not issued as of 2026-06-24. "
    'Escalated twice with no response from finance team.", "priority": "high"}'
)

anthropic_adapter = AnthropicMessagesAdapter()
agent_run_id = "run-agent-b-1"
llm_turns_usage = [
    # (input_tokens, cache_read, cache_creation, output_tokens)
    (450, 0, 0, 60),  # turn 1: fresh context, plans to call search_tickets
    (620, 380, 90, 75),  # turn 2: search results appended; some of turn-1 context cached
    (810, 560, 140, 95),  # turn 3: ticket detail appended; cache grows further
    (960, 720, 110, 220),  # turn 4: final summary drafted (longer completion)
]

with trace(business_id="northwind", workflow="support_agent") as root_b:
    trace_b = Trace(trace_id=root_b.trace_id, business_id="northwind", workflow="support_agent")

    with span() as step0_ctx:
        plan_step = new_agent_span(
            trace_b.trace_id,
            agent_run_id=agent_run_id,
            step_index=0,
            step_type="plan",
            parent_span_id=root_b.span_id,
            span_id=step0_ctx.span_id,
            loop_count=0,
            memory_read_count=1,
        )
        trace_b.add_span(plan_step)
        u = llm_turns_usage[0]
        ev0 = normalize(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": u[0],
                    "output_tokens": u[3],
                    "cache_read_input_tokens": u[1],
                    "cache_creation_input_tokens": u[2],
                },
            },
            anthropic_adapter,
            context=step0_ctx,
        )
        trace_b.add_event(ev0)

    with span() as tool1_ctx:
        tool1 = new_tool_span(
            trace_b.trace_id, tool_name="search_tickets", tool_call_id="call-1", parent_span_id=root_b.span_id, span_id=tool1_ctx.span_id
        )
        record_tool_result(tool1, result_text=TICKET_SEARCH_RESULT, injected_into_context=True)
        trace_b.add_span(tool1)

    with span() as step1_ctx:
        agent_step1 = new_agent_span(
            trace_b.trace_id,
            agent_run_id=agent_run_id,
            step_index=1,
            step_type="tool_call",
            parent_span_id=root_b.span_id,
            span_id=step1_ctx.span_id,
            loop_count=1,
            memory_read_count=1,
            memory_write_count=1,
        )
        trace_b.add_span(agent_step1)
        u = llm_turns_usage[1]
        ev1 = normalize(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": u[0],
                    "output_tokens": u[3],
                    "cache_read_input_tokens": u[1],
                    "cache_creation_input_tokens": u[2],
                },
            },
            anthropic_adapter,
            context=step1_ctx,
        )
        trace_b.add_event(ev1)

    with span() as tool2_ctx:
        tool2 = new_tool_span(
            trace_b.trace_id,
            tool_name="get_ticket_details",
            tool_call_id="call-2",
            parent_span_id=root_b.span_id,
            span_id=tool2_ctx.span_id,
        )
        record_tool_result(tool2, result_text=TICKET_DETAIL_RESULT, injected_into_context=True)
        trace_b.add_span(tool2)

    with span() as step2_ctx:
        agent_step2 = new_agent_span(
            trace_b.trace_id,
            agent_run_id=agent_run_id,
            step_index=2,
            step_type="tool_call",
            parent_span_id=root_b.span_id,
            span_id=step2_ctx.span_id,
            loop_count=2,
            memory_read_count=2,
            memory_write_count=2,
        )
        trace_b.add_span(agent_step2)
        u = llm_turns_usage[2]
        ev2 = normalize(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": u[0],
                    "output_tokens": u[3],
                    "cache_read_input_tokens": u[1],
                    "cache_creation_input_tokens": u[2],
                },
            },
            anthropic_adapter,
            context=step2_ctx,
        )
        trace_b.add_event(ev2)

    with span() as step3_ctx:
        agent_step3 = new_agent_span(
            trace_b.trace_id,
            agent_run_id=agent_run_id,
            step_index=3,
            step_type="final_answer",
            parent_span_id=root_b.span_id,
            span_id=step3_ctx.span_id,
            loop_count=3,
            max_steps_reached=False,
            memory_read_count=2,
            memory_write_count=3,
        )
        trace_b.add_span(agent_step3)
        u = llm_turns_usage[3]
        ev3 = normalize(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": u[0],
                    "output_tokens": u[3],
                    "cache_read_input_tokens": u[1],
                    "cache_creation_input_tokens": u[2],
                },
            },
            anthropic_adapter,
            context=step3_ctx,
        )
        trace_b.add_event(ev3)

    with span() as maxed_ctx:
        maxed_step = new_agent_span(
            trace_b.trace_id,
            agent_run_id="run-agent-b-maxed",
            step_index=10,
            step_type="tool_call",
            parent_span_id=root_b.span_id,
            span_id=maxed_ctx.span_id,
            loop_count=10,
            max_steps_reached=True,
            retry_count=2,
        )
        trace_b.add_span(maxed_step)

expected_b = sum(sum(u) for u in llm_turns_usage)  # Anthropic buckets are ALL additive
grand_total_b = observed_total_contributing_tokens(trace_b)
check(
    grand_total_b == expected_b,
    f"Scenario B: 4-turn agent run (Anthropic, additive cache) reconciles exactly (got {grand_total_b}, expected {expected_b})",
)

tool_estimates = [tool1.metadata["tool_result_estimated_tokens"], tool2.metadata["tool_result_estimated_tokens"]]
check(all(t > 20 for t in tool_estimates), f"Scenario B: tool-result estimates are substantial, not trivial {tool_estimates}")
check(
    grand_total_b != expected_b + sum(tool_estimates),
    "Scenario B: tool-result token ESTIMATES never get added on top of the real LLM totals",
)
check(agent_step3.metadata["max_steps_reached"] is False, "Scenario B: agent completed within budget (max_steps_reached=False)")
check(
    maxed_step.metadata["max_steps_reached"] is True and maxed_step.metadata["loop_count"] == 10,
    "Scenario B variant: a separate agent step correctly records hitting the step budget",
)
check(len(trace_b.spans) == 7, f"Scenario B: 5 agent-step spans + 2 tool spans recorded (got {len(trace_b.spans)})")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
