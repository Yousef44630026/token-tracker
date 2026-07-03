"""DEEP realistic scenario C — a long real-prose streamed answer, interrupted 60% through
(estimate from real accumulated text, sanity-checked against the true final length), then
reconnected.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_scenario_c_stream_interrupted.py

Split out of the original test_realistic_scenarios_deep.py — see test_scenario_a_rag_conversation.py
for the sibling scenarios and the reasoning behind the split.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.estimation.local_tokenizer import estimate_tokens  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


print("\n--- Scenario C: long real-prose stream, interrupted then reconnected ---")

FULL_ANSWER = (
    "Based on our records, your order NW-559214 was returned to our warehouse on June 17th "
    "and confirmed as received in good condition. Under our standard 30-day return policy, "
    "you're entitled to a full refund of $142.50 to your original payment method. I can see "
    "this refund was delayed beyond our usual 5-7 business day window, and I sincerely "
    "apologize for the inconvenience this has caused. I've escalated your case directly to "
    "our finance team with priority handling, and you should see the refund posted to your "
    "account within the next 2 business days. I'll also apply a $15 credit to your account "
    "for the delay. Please let me know if there's anything else I can help you with today."
)
words = FULL_ANSWER.split(" ")
chunks = [" ".join(words[i : i + 4]) + " " for i in range(0, len(words), 4)]  # ~4-word streaming deltas
check(len(chunks) >= 25, f"Scenario C: the response is chunked into a substantial number of stream deltas ({len(chunks)})")

interrupt_point = int(len(chunks) * 0.6)


def openai_delta_text(event):
    choices = event.get("choices") or []
    return choices[0].get("delta", {}).get("content") if choices else None


interrupted_stream = [
    {"choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}]} for c in chunks[:interrupt_point]
]  # connection drops before the final usage chunk ever arrives

with trace(business_id="northwind", workflow="rag_support") as root_c:
    with span() as stream_ctx1:
        partial_ev = consume_stream(
            interrupted_stream, OpenAIChatCompletionsAdapter(), context=stream_ctx1, text_extractor=openai_delta_text
        )

    partial_out = q(partial_ev, TokenType.OUTPUT)
    check(partial_out.precision_level == PrecisionLevel.ESTIMATE, "Scenario C: interrupted mid-stream -> ESTIMATE")
    partial_text = "".join(chunks[:interrupt_point])
    expected_estimate = estimate_tokens(partial_text)
    check(
        partial_out.quantity == expected_estimate,
        f"Scenario C: partial estimate matches the local tokenizer applied to the REAL "
        f"accumulated prose ({partial_out.quantity} == {expected_estimate})",
    )
    check(
        partial_out.quantity > 20,
        f"Scenario C: the estimate from ~60% of a substantial answer is itself substantial "
        f"({partial_out.quantity} tokens, {len(partial_text)} chars)",
    )

    # reconnect: the true final usage for the FULL (complete) answer arrives, same context
    full_completion_tokens = 165  # a realistic count for the full ~165-word answer
    final_ev = consume_stream(
        [
            {
                "choices": [],
                "usage": {"prompt_tokens": 540, "completion_tokens": full_completion_tokens, "total_tokens": 540 + full_completion_tokens},
            }
        ],
        OpenAIChatCompletionsAdapter(),
        context=stream_ctx1,
    )

reconcile_supersession([partial_ev, final_ev])
check(
    partial_ev.superseded is True and partial_ev.event_contributing_tokens == 0,
    "Scenario C: the partial estimate is superseded once the true final arrives, contributes 0",
)
check(
    final_ev.event_contributing_tokens == 540 + full_completion_tokens, "Scenario C: the final's real usage is the only thing that counts"
)
check(
    abs(partial_out.quantity - full_completion_tokens * 0.6) < full_completion_tokens * 0.6,
    "Scenario C: the ESTIMATE was at least in the right ballpark of 60% of the true final "
    "output (sanity check on the estimator, not just plumbing)",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
