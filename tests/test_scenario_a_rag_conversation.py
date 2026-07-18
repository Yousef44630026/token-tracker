"""DEEP realistic scenario A — 4-turn RAG support conversation with a real policy document
and GROWING OpenAI prompt-cache hits across turns.

Run: python tests/test_scenario_a_rag_conversation.py

Split out of the original test_realistic_scenarios_deep.py (which held 5 unrelated scenarios
in one 397-line file) so each narrative stands alone and is easier to navigate. See also:
test_scenario_b_agent_tool_calls.py, _c_stream_interrupted.py, _d_cross_provider_failover.py,
_e_duplicate_delivery.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context, record_vector_search  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


print("\n--- Scenario A: RAG support conversation, 4 turns, growing prompt cache ---")

SYSTEM_PROMPT = (
    "You are Atlas, the customer support assistant for Northwind Traders. Always answer "
    "using ONLY the retrieved policy context provided below. If the context does not cover "
    "the customer's question, say you will escalate to a human agent. Be concise, polite, "
    "and never invent a policy detail that is not explicitly stated in the retrieved text. "
    "When quoting a deadline or a monetary amount, quote it exactly as written in the source."
)

POLICY_DOCUMENT = (
    "Northwind Traders Return & Refund Policy (revised January 2026)\n\n"
    "1. Standard returns: items may be returned within 30 days of delivery for a full refund "
    "to the original payment method, provided the item is unused and in its original "
    "packaging. Refunds are processed within 5-7 business days of the warehouse receiving "
    "the returned item.\n\n"
    "2. Final-sale items: clearance items marked 'Final Sale' at checkout cannot be returned "
    "or exchanged, except in the case of a manufacturing defect confirmed by our quality team.\n\n"
    "3. Late returns (31-60 days): a store-credit-only refund may be issued at 80% of the "
    "purchase price, subject to manager approval.\n\n"
    "4. International orders: return shipping for international orders is the customer's "
    "responsibility unless the item arrived damaged or was shipped in error."
)

adapter_a = OpenAIChatCompletionsAdapter()
turn_events = []
running_total = 0

with trace(business_id="northwind", workflow="rag_support") as root_a:
    trace_a = Trace(trace_id=root_a.trace_id, business_id="northwind", workflow="rag_support")

    with span() as vs_ctx:
        vs = new_rag_span(trace_a.trace_id, "vector_search", parent_span_id=root_a.span_id, span_id=vs_ctx.span_id)
        record_vector_search(vs, num_results=3, latency_ms=42.7, index="northwind-policies-v3")
        trace_a.add_span(vs)

    with span() as pa_ctx:
        pa = new_rag_span(trace_a.trace_id, "prompt_assembly", parent_span_id=root_a.span_id, span_id=pa_ctx.span_id)
        record_retrieved_context(pa, context_text=POLICY_DOCUMENT, injected_into_prompt=True)
        trace_a.add_span(pa)

    retrieved_estimate = pa.metadata["retrieved_context_estimated_tokens"]
    check(retrieved_estimate > 50, f"Scenario A: retrieved policy doc estimate is substantial ({retrieved_estimate} tokens)")

    # Turn-by-turn: system prompt + policy doc + growing conversation history means
    # prompt_tokens grows each turn, and OpenAI's automatic prefix cache increasingly
    # covers the STABLE prefix (system + policy + earlier turns), so cached_tokens grows too.
    turns = [
        # (prompt_tokens, cached_tokens, completion_tokens, total_tokens)
        (620, 0, 85, 705),  # turn 1: nothing cached yet (first call with this prefix)
        (740, 512, 95, 835),  # turn 2: the stable prefix starts getting cache hits
        (860, 640, 110, 970),  # turn 3: cache coverage grows further
        (990, 768, 78, 1068),  # turn 4: still growing
    ]
    for i, (prompt_t, cached_t, completion_t, total_t) in enumerate(turns, start=1):
        payload = {
            "id": f"chatcmpl-northwind-turn{i}",
            "object": "chat.completion",
            "model": "gpt-4o-2024-08-06",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Thanks for reaching out! Per our return policy, standard items can be "
                            "returned within 30 days for a full refund, provided they're unused and in "
                            "original packaging. Let me know if you'd like the prepaid return label."
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_t,
                "completion_tokens": completion_t,
                "total_tokens": total_t,
                "prompt_tokens_details": {"cached_tokens": cached_t},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        with span() as turn_ctx:
            ev = normalize(payload, adapter_a, context=turn_ctx)
        trace_a.add_event(ev)
        turn_events.append(ev)
        check(ev.event_total_mismatch == 0, f"Scenario A turn {i}: reconciles ({ev.event_contributing_tokens} == {total_t})")
        cached_q = q(ev, TokenType.CACHED_INPUT)
        if cached_t == 0:
            # the adapter treats a real zero cache hit as "nothing to report" (no quantity
            # created at all) — this is the documented, intentional behavior; assert it
            # explicitly rather than assume, so this turn's zero is a locked-in fact too.
            check(cached_q is None, f"Scenario A turn {i}: a genuine 0 cached_tokens creates NO cached_input quantity (by design)")
        else:
            check(
                cached_q.quantity == cached_t and cached_q.quantity_in_total == 0,
                f"Scenario A turn {i}: cached_tokens ({cached_t}) recorded but contributes 0 (subtotal of input)",
            )
        running_total += total_t

cache_progression = [(q(ev, TokenType.CACHED_INPUT).quantity if q(ev, TokenType.CACHED_INPUT) else 0) for ev in turn_events]
check(
    cache_progression == sorted(cache_progression),
    f"Scenario A: cache hits GROW monotonically across turns as history accumulates {cache_progression}",
)

grand_total_a = observed_total_contributing_tokens(trace_a)
check(grand_total_a == running_total, f"Scenario A: 4-turn conversation total reconciles exactly ({grand_total_a} == {running_total})")
check(
    grand_total_a != retrieved_estimate + running_total,
    "Scenario A: the RAG retrieved-context ESTIMATE is never added on top of the real LLM totals (no double count)",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
