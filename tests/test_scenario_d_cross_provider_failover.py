"""DEEP realistic scenario D — a cross-provider failover mid-conversation (OpenAI outage ->
Anthropic takeover) with the SAME substantial context resent: both legitimately contribute
(unlike a same-provider duplicate, this is genuinely two separate calls).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_scenario_d_cross_provider_failover.py

Split out of the original test_realistic_scenarios_deep.py — see test_scenario_a_rag_conversation.py
for the sibling scenarios and the reasoning behind the split.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


print("\n--- Scenario D: cross-provider failover, both providers' costs are real and DO count ---")

with trace(business_id="northwind", workflow="rag_support_failover") as root_d:
    trace_d = Trace(trace_id=root_d.trace_id, business_id="northwind", workflow="rag_support_failover")

    # turn 1: OpenAI receives the substantial question, but the provider call fails outright
    # (simulated outage: response has no usable usage object at all)
    with span() as d1_ctx:
        ev_d1 = normalize({"id": "chatcmpl-fail", "choices": []}, OpenAIChatCompletionsAdapter(), context=d1_ctx)
    trace_d.add_event(ev_d1)
    check(
        "raw_usage_missing" in ev_d1.data_quality_flags and ev_d1.event_contributing_tokens == 0,
        "Scenario D: the failed OpenAI attempt contributes 0 tokens and is flagged (genuinely no usage happened)",
    )

    # failover: the SAME substantial question is resent to Anthropic, which succeeds
    with span() as d2_ctx:
        ev_d2 = normalize(
            {
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": 380,
                    "output_tokens": 140,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
            AnthropicMessagesAdapter(),
            context=d2_ctx,
        )
    trace_d.add_event(ev_d2)
    check(ev_d2.event_contributing_tokens == 520, "Scenario D: the Anthropic failover call contributes its real, distinct cost")

    # a SECOND (successful, different) OpenAI call later in the SAME conversation
    with span() as d3_ctx:
        ev_d3 = normalize(
            {
                "choices": [{"message": {"content": "Anything else?"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 610, "completion_tokens": 12, "total_tokens": 622},
            },
            OpenAIChatCompletionsAdapter(),
            context=d3_ctx,
        )
    trace_d.add_event(ev_d3)

by_provider = {}
for e in trace_d.events:
    by_provider.setdefault(e.provider, 0)
    by_provider[e.provider] += e.event_contributing_tokens
check(
    by_provider.get("openai", 0) == 622,
    f"Scenario D: OpenAI's total correctly attributes only its OWN successful calls (622), "
    f"not the failed one (got {by_provider.get('openai')})",
)
check(by_provider.get("anthropic", 0) == 520, "Scenario D: Anthropic's failover total is attributed separately (520)")
grand_total_d = observed_total_contributing_tokens(trace_d)
check(
    grand_total_d == 622 + 520,
    f"Scenario D: grand total includes BOTH providers' real costs (the resend to Anthropic "
    f"is a genuinely separate cost, not a duplicate) (got {grand_total_d})",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
