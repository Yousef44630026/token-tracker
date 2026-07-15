"""REAL Anthropic Messages payload — the cache-bucket additivity is verified, not assumed.

INV-4 claims that for Anthropic, input / cache_read / cache_creation / output are SEPARATE
additive input buckets (all total_contributing) — cache tokens are NOT contained inside
input_tokens the way OpenAI's cached_tokens are. Until now that rule rested on a SIMULATED
fixture built to the documented shape, i.e. on our own assumption.

This test pins it to a RECORDED REAL response (usage verbatim from a live Claude Code session;
prompt/response content stripped — the tracker never stores raw text).

The falsification is arithmetic and decisive: the real turn reports input_tokens=2 while
cache_creation_input_tokens=866255 and cache_read_input_tokens=18023. If the cache buckets were
subtotals CONTAINED in input_tokens, then input_tokens >= cache_read would have to hold. Two
tokens cannot contain 866255. Containment is therefore impossible, and total_contributing
(separate additive buckets) is the only assignment consistent with the real data.

Anthropic reports no total_tokens, so provider_total_tokens is None and there is no total to
reconcile against — which is exactly why this structural falsification matters: the usual
sum-vs-provider-total safety net does not exist for this provider.

Run: python tests/test_real_payload_anthropic.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, Overlap, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "anthropic_messages_cache.REAL.json")

with open(FIXTURE, encoding="utf-8") as handle:
    raw = json.load(handle)

check(raw.get("_REAL") is True, "fixture is marked REAL (recorded, not hand-built)")
payload = raw["response"]
usage = payload["usage"]

# --- the recorded facts the falsification rests on -------------------------------------
inp = usage["input_tokens"]
cache_read = usage["cache_read_input_tokens"]
cache_creation = usage["cache_creation_input_tokens"]
out = usage["output_tokens"]

check(inp < cache_read, f"REAL: input_tokens ({inp}) < cache_read_input_tokens ({cache_read}) — containment impossible")
check(inp < cache_creation, f"REAL: input_tokens ({inp}) < cache_creation_input_tokens ({cache_creation}) — containment impossible")

# --- the adapter's assignment must follow the real data ---------------------------------
event = normalize(payload, AnthropicMessagesAdapter(), context=new_trace())
by_type = {q.token_type: q for q in event.quantities}

REAL_BUCKETS = {TokenType.INPUT, TokenType.OUTPUT, TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT}
check(set(by_type) == REAL_BUCKETS, "all four real buckets extracted")

for token_type in (TokenType.INPUT, TokenType.OUTPUT, TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT):
    quantity = by_type[token_type]
    check(
        quantity.additivity == Additivity.TOTAL_CONTRIBUTING and quantity.overlap == Overlap.INDEPENDENT,
        f"{token_type.value}: total_contributing / independent (a separate additive bucket)",
    )

check(
    by_type[TokenType.CACHED_INPUT].subtotal_of is None,
    "cached_input is NOT subtotal_of input for Anthropic (unlike OpenAI) — proven by the real payload",
)

# --- totals ------------------------------------------------------------------------------
check(event.provider_total_tokens is None, "Anthropic reports no total_tokens -> provider_total_tokens is None")
check(event.event_total_mismatch is None, "no provider total -> no mismatch to compute (structural check is the safety net here)")
check(
    event.event_contributing_tokens == inp + cache_read + cache_creation + out,
    f"contributing == sum of the four real buckets ({inp + cache_read + cache_creation + out})",
)
check(event.data_quality_flags == [], "a clean real response raises no data-quality flag")

# --- forward-compat: the real payload carries fields the adapter does not model ----------
# (server_tool_use, service_tier, cache_creation breakdown, iterations, speed, inference_geo)
# Unmodelled fields must be ignored without breaking extraction or inventing tokens.
check(
    "iterations" in usage and "service_tier" in usage,
    "the REAL payload carries fields absent from the simulated shape (recorded drift surface)",
)
check(
    event.event_contributing_tokens == inp + cache_read + cache_creation + out,
    "unmodelled real fields do not leak into the total",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
