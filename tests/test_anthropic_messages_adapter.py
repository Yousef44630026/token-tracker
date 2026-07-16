"""Anthropic Messages adapter: cache buckets contribute separately from fresh input.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_anthropic_messages_adapter.py

Anthropic reports input/output and separate cache_* counts but no provider total. Official
prompt-caching semantics and a real Claude Code capture confirm that input, cache read, and
cache creation are distinct contributing input buckets.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def by_type(usage, tt):
    return next((q for q in usage.quantities if q.token_type == tt), None)


with open(os.path.join(FIXTURES, "anthropic_messages_cache.SIMULATED.json"), encoding="utf-8") as f:
    payload = json.load(f)["response"]

usage = AnthropicMessagesAdapter().extract_usage_from_response(payload)

inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)
cread = by_type(usage, TokenType.CACHED_INPUT)
cwrite = by_type(usage, TokenType.CACHE_CREATION_INPUT)

check(inp.quantity == 1000 and inp.additivity == Additivity.TOTAL_CONTRIBUTING, "input total_contributing")
check(out.quantity == 300 and out.additivity == Additivity.TOTAL_CONTRIBUTING, "output total_contributing")
check(cread is not None and cread.additivity == Additivity.TOTAL_CONTRIBUTING, "cache_read contributes separately")
check(cwrite is not None and cwrite.additivity == Additivity.TOTAL_CONTRIBUTING, "cache_creation contributes separately")
check(cread.quantity_in_total == 800 and cwrite.quantity_in_total == 120, "cache buckets contribute their exact counts")
check(cread.export_warning is None and cwrite.export_warning is None, "verified cache buckets have no warning")

check(usage.provider_total_tokens is None, "Anthropic provides no total -> provider_total_tokens is None")

event = TokenEvent(
    event_id="evt-anthropic",
    request_correlation_id="r-a",
    trace_id="t-1",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
    observation={"authoritative": True},
)
check(event.event_contributing_tokens == 2220, "contributing == input+cache read+cache creation+output")
check(event.event_total_mismatch is None, "no provider total -> mismatch is None (cannot be judged)")

flags = normalizer_flags(usage.quantities, usage.provider_total_tokens)
check("unverified_additivity" not in flags, "verified Anthropic cache raises no additivity warning")
check("provider_total_mismatch" not in flags, "no provider_total_mismatch (no total to compare)")

# --- through the keystone ---
ev = normalize(payload, AnthropicMessagesAdapter(), context=new_trace())
check(ev.provider == "anthropic" and ev.event_contributing_tokens == 2220, "normalize() yields an anthropic event (2220)")
check("unverified_additivity" not in ev.data_quality_flags, "normalize() treats Anthropic cache as verified")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
