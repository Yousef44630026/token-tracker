"""Regression — fresh_input_tokens must mean the same thing regardless of cache additivity style.

Run: python tests/test_cache_fresh_tokens_regression.py

Found during a rigorous logic/relevance review of tracker/analytics/cache.py: the old formula
read TokenType.INPUT directly. For OpenAI (cache is subtotal_of input), the raw input_tokens
ALREADY includes the cached portion, so "fresh" silently meant "everything, cache-inclusive".
For Anthropic (cache is a separate additive bucket), input_tokens already excludes cache, so
the SAME field name meant something genuinely different depending on which provider produced
the event, with no way to tell from the output alone. Fixed by deriving fresh_input_tokens from
prompt_input_tokens minus verified cache — correct and consistent for both styles.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.analytics.cache import build_cache_summary  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# OpenAI-style: raw input_tokens=1000 ALREADY includes 400 cached tokens (subtotal_of input)
tr_openai = Trace(trace_id="cache-fresh-openai")
ev_openai = normalize(
    {
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "prompt_tokens_details": {"cached_tokens": 400},
        },
    },
    OpenAIChatCompletionsAdapter(),
    context=new_trace(trace_id="cache-fresh-openai"),
)
tr_openai.add_event(ev_openai)
openai_summary = build_cache_summary(tr_openai)
check(openai_summary["prompt_input_tokens"] == 1000, "OpenAI: prompt_input_tokens is the full 1000 (cache-inclusive raw value)")
check(openai_summary["cache_read_tokens"] == 400, "OpenAI: cache_read_tokens == 400")
check(
    openai_summary["fresh_input_tokens"] == 600,
    f"FIXED: OpenAI fresh_input_tokens correctly subtracts the cached portion (1000-400=600), "
    f"the old code returned 1000 here, ignoring the cache entirely (got {openai_summary['fresh_input_tokens']})",
)

# Anthropic-style: input_tokens=600 is ALREADY fresh-only; cache_read=400 is a separate additive bucket
tr_anthropic = Trace(trace_id="cache-fresh-anthropic")
ev_anthropic = normalize(
    {
        "model": "claude-opus-4-8",
        "usage": {"input_tokens": 600, "output_tokens": 100, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 0},
    },
    AnthropicMessagesAdapter(),
    context=new_trace(trace_id="cache-fresh-anthropic"),
)
tr_anthropic.add_event(ev_anthropic)
anthropic_summary = build_cache_summary(tr_anthropic)
check(anthropic_summary["prompt_input_tokens"] == 1000, "Anthropic: prompt_input_tokens == 600 fresh + 400 cache == 1000")
check(anthropic_summary["cache_read_tokens"] == 400, "Anthropic: cache_read_tokens == 400")
check(
    anthropic_summary["fresh_input_tokens"] == 600,
    f"Anthropic fresh_input_tokens correctly matches the raw fresh input (600) " f"(got {anthropic_summary['fresh_input_tokens']})",
)

check(
    openai_summary["fresh_input_tokens"] == anthropic_summary["fresh_input_tokens"] == 600,
    "SAME cache-adjusted meaning across both additivity styles, not provider-dependent silently",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
