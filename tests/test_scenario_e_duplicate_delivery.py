"""DEEP realistic scenario E — the duplicate-final-delivery bug (fixed in
tracker/normalization/supersession.py), framed as a realistic at-least-once webhook incident
on a substantial legal-summary completion, with real token counts.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_scenario_e_duplicate_delivery.py

Split out of the original test_realistic_scenarios_deep.py — see test_scenario_a_rag_conversation.py
for the sibling scenarios and the reasoning behind the split.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


print("\n--- Scenario E: at-least-once webhook delivers the same completion twice ---")

LEGAL_SUMMARY_USAGE = {"prompt_tokens": 4200, "completion_tokens": 850, "total_tokens": 5050}

with trace(business_id="northwind", workflow="legal_summary") as root_e:
    with span() as e_ctx:
        first_delivery = normalize(
            {"model": "gpt-4o", "usage": LEGAL_SUMMARY_USAGE},
            OpenAIChatCompletionsAdapter(),
            context=e_ctx,
            event_id="evt-legal-8842-delivery-1",
            timestamp="2026-06-30T14:02:10Z",
        )
        # a network hiccup causes the SAME underlying completion to be delivered again 3
        # seconds later, under a DIFFERENT event_id but the SAME request_correlation_id
        second_delivery = normalize(
            {"model": "gpt-4o", "usage": LEGAL_SUMMARY_USAGE},
            OpenAIChatCompletionsAdapter(),
            context=e_ctx,
            event_id="evt-legal-8842-delivery-2",
            timestamp="2026-06-30T14:02:13Z",
        )

check(
    first_delivery.request_correlation_id == second_delivery.request_correlation_id,
    "Scenario E: both deliveries share the same request_correlation_id (same underlying attempt)",
)
check(
    first_delivery.event_id != second_delivery.event_id,
    "Scenario E: but they are distinct events (different event_id, as a real duplicate delivery would be)",
)

reconcile_supersession([first_delivery, second_delivery])
total_e = first_delivery.event_contributing_tokens + second_delivery.event_contributing_tokens
check(total_e == 5050, f"Scenario E FIXED: the duplicate webhook delivery contributes 5050 tokens ONCE, not 10100 (got {total_e})")
# the tie-break prefers the LATEST timestamp as authoritative (presumed the more complete /
# final measurement), so the EARLIER delivery is the one demoted here
check(
    first_delivery.superseded is True and first_delivery.superseded_by == "evt-legal-8842-delivery-2",
    "Scenario E: the earlier-timestamped delivery is superseded by the later one",
)
check(
    second_delivery.superseded is False and second_delivery.event_contributing_tokens == 5050,
    "Scenario E: the LATEST-timestamped delivery is kept authoritative and is the one that contributes",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
