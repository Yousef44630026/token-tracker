"""Mistral OpenAI-style sub-details fail CLOSED to unverified (INV-4), by design.

MistralChatAdapter subclasses OpenAIChatCompletionsAdapter, so it INHERITS the extraction that
reads prompt_tokens_details.cached_tokens / completion_tokens_details.reasoning_tokens. If a
Mistral response carries those fields, they are extracted — but the INV-4 table registers only
mistral input/output, so cached_input/reasoning fall through to the fail-closed default
additivity="unverified" rather than OpenAI's subtotal_of.

This is intentional: Mistral's cache/reasoning additivity has NOT been verified against a real
recorded payload, and INV-4 requires unfamiliar (provider, token_type) combos to fail closed
rather than be assumed identical to another provider's. The numeric total is unaffected (a
subtotal and an unverified quantity both contribute 0); the difference is that the unverified
path honestly flags the unproven additivity instead of silently trusting it.

This test pins that contract so nobody "fixes" it by assuming Mistral == OpenAI, and so that if
mistral cache/reasoning rows are ever added to the table (after a real payload), this test is the
place that must be updated deliberately.

Run: python tests/test_mistral_detail_fields_fail_closed.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.mistral_chat_adapter import MistralChatAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, Overlap, TokenType, Trust  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# An OpenAI-compatible payload that DOES carry cache + reasoning sub-details.
PAYLOAD = {
    "model": "mistral-large-latest",
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "total_tokens": 1300,
        "prompt_tokens_details": {"cached_tokens": 800},
        "completion_tokens_details": {"reasoning_tokens": 250},
    },
}

mistral = normalize(PAYLOAD, MistralChatAdapter(), context=new_trace())
openai = normalize(PAYLOAD, OpenAIChatCompletionsAdapter(), context=new_trace())

m = {q.token_type: q for q in mistral.quantities}
o = {q.token_type: q for q in openai.quantities}

# --- the inherited extraction DID read the sub-details (they are not dropped) ---
check(TokenType.CACHED_INPUT in m and TokenType.REASONING in m, "Mistral inherits extraction and reads cached/reasoning sub-details")

# --- but they fail CLOSED to unverified (not OpenAI's subtotal_of) ---
check(
    m[TokenType.CACHED_INPUT].additivity == Additivity.UNVERIFIED
    and m[TokenType.CACHED_INPUT].trust == Trust.UNVERIFIED
    and m[TokenType.CACHED_INPUT].overlap == Overlap.INDEPENDENT,
    "Mistral cached_input fails closed to unverified (semantics not verified against a real payload)",
)
check(
    m[TokenType.REASONING].additivity == Additivity.UNVERIFIED,
    "Mistral reasoning fails closed to unverified",
)

# --- the intentional divergence from the OpenAI sibling, documented ---
check(
    o[TokenType.CACHED_INPUT].additivity == Additivity.SUBTOTAL_OF and o[TokenType.CACHED_INPUT].subtotal_of == "input",
    "OpenAI (verified) treats cached_input as subtotal_of input — the divergence is deliberate, not a copy bug",
)

# --- totals are IDENTICAL and correct either way (both contribute 0) ---
check(mistral.event_contributing_tokens == 1300, "Mistral total = input+output (1300); unverified sub-details contribute 0")
check(
    mistral.event_contributing_tokens == openai.event_contributing_tokens,
    "same numeric total as OpenAI — the fail-closed path never changes the count, only the flag",
)
check(mistral.event_total_mismatch == 0, "no provider-total mismatch: sum(quantity_in_total) == provider_total")

# --- and it is honestly surfaced as unverified rather than silently trusted ---
check(
    "unverified_additivity" in mistral.data_quality_flags,
    "Mistral event honestly raises unverified_additivity for the unproven sub-details",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
