"""Verification audit - guard against raw quantity double counting.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_double_count_guard.py

Builds deliberately crowded OpenAI and Anthropic events. The derived total must equal only
the additive buckets, never the raw sum of all visible usage quantities.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def quantities(event, token_type):
    return [quantity for quantity in event.quantities if quantity.token_type == token_type]


def one(event, token_type):
    found = quantities(event, token_type)
    return found[0] if found else None


openai_response = {
    "model": "gpt-4o-audit",
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "total_tokens": 1300,
        "prompt_tokens_details": {
            "cached_tokens": 800,
            "audio_tokens": 400,
        },
        "completion_tokens_details": {
            "reasoning_tokens": 250,
            "audio_tokens": 100,
        },
    },
}
event = normalize(openai_response, OpenAIChatCompletionsAdapter(), context=new_trace())
raw_sum = sum(quantity.quantity or 0 for quantity in event.quantities)
qit_sum = sum(quantity.quantity_in_total for quantity in event.quantities)
check(raw_sum == 2850, f"OpenAI: raw visible quantity sum is crowded (got {raw_sum})")
check(qit_sum == 1300, "OpenAI: sum(quantity_in_total) counts input + output only")
check(event.event_contributing_tokens == 1300, "OpenAI: event_contributing_tokens == additive buckets")
check(event.event_total_mismatch == 0, "OpenAI: provider total reconciles")
for token_type in (TokenType.CACHED_INPUT, TokenType.REASONING, TokenType.AUDIO_INPUT, TokenType.AUDIO_OUTPUT):
    quantity = one(event, token_type)
    check(
        quantity is not None and quantity.additivity == Additivity.SUBTOTAL_OF and quantity.quantity_in_total == 0,
        f"OpenAI: {token_type.value} is a non-contributing subtotal",
    )

anthropic_response = {
    "model": "claude-audit",
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 300,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 250,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 200,
            "ephemeral_1h_input_tokens": 50,
        },
    },
}
event = normalize(anthropic_response, AnthropicMessagesAdapter(), context=new_trace())
raw_sum = sum(quantity.quantity or 0 for quantity in event.quantities)
qit_sum = sum(quantity.quantity_in_total for quantity in event.quantities)
check(raw_sum == 2350, f"Anthropic: raw visible quantity sum is additive (got {raw_sum})")
check(qit_sum == 2350, "Anthropic: cache read/creation are separate additive buckets")
check(event.event_contributing_tokens == 2350, "Anthropic: event_contributing_tokens includes cache buckets")
check(event.event_total_mismatch is None, "Anthropic: no provider total is fabricated")
for token_type in (TokenType.INPUT, TokenType.OUTPUT, TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT):
    quantity = one(event, token_type)
    check(
        quantity is not None and quantity.additivity == Additivity.TOTAL_CONTRIBUTING,
        f"Anthropic: {token_type.value} is total_contributing",
    )
check("unverified_additivity" not in event.data_quality_flags, "Anthropic: verified cache buckets raise no unverified flag")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
