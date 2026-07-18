"""Regression coverage for Power BI exporter bugs, including duplicated analytics mistakes
the Python analytics layer, reintroduced here because this exporter reimplements its own
copies of _cloud_provider/_is_error/cache-rate logic instead of reusing the fixed modules.

Run: python tests/test_powerbi_exporter_regression.py

1. _cloud_provider() merged direct Gemini and Vertex AI under "gcp" (same bug as
   service_attribution.py, independently duplicated here).
2. error_count silently read 0 (and the DAX Success Rate measure silently read 100%) for any
   event with no `observation` data — the same false-confident-zero as reliability.py had.
   Fixed via a new `measured` column; Success/Error Rate now divide by Measured Events.
3. `Tokens Per Successful Event` DAX summed ALL events' tokens (including failed ones) but
   divided by only the successful COUNT — the same tokens_per_successful_agent_run bug.
4. `Cache Hit Rate` DAX divided by (raw input_tokens + cached_input_tokens): for OpenAI-style
   providers, raw input_tokens already includes the cached portion, double-counting it in the
   denominator. Fixed with a new provider-consistent `fresh_input_tokens` column.
5. Event breakdown columns rendered an unknown quantity as numeric zero, erasing INV-6's
   distinction between unknown and a provider-reported zero.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.export.powerbi_exporter import dax_measures, fact_token_event_rows  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# --- 1. Gemini vs Vertex AI cloud attribution ---
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UnknownReason, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402


def q(qty):
    return TokenQuantity(TokenType.INPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


gemini_event = TokenEvent(
    event_id="gemini-1", request_correlation_id="r1", trace_id="t", span_id="s", provider="gemini", quantities=[q(10)],
    observation={"authoritative": True},
)
vertex_event = TokenEvent(
    event_id="vertex-1", request_correlation_id="r2", trace_id="t", span_id="s", provider="vertex_ai", quantities=[q(10)],
    observation={"authoritative": True},
)
rows = fact_token_event_rows([gemini_event, vertex_event])
gemini_row = next(r for r in rows if r["event_id"] == "gemini-1")
vertex_row = next(r for r in rows if r["event_id"] == "vertex-1")
check(vertex_row["cloud_provider"] == "gcp", "1. Vertex AI still attributes to gcp")
check(gemini_row["cloud_provider"] != "gcp", f"1. FIXED: direct Gemini no longer merged into gcp (got {gemini_row['cloud_provider']!r})")

# --- 2. unmeasured event no longer silently error_count=0-implies-success ---
unmeasured_event = TokenEvent(
    event_id="unmeasured-1", request_correlation_id="r3", trace_id="t", span_id="s", provider="openai", quantities=[q(10)],
    observation={"authoritative": True},
)
measured_row = fact_token_event_rows([unmeasured_event])[0]
check(measured_row["measured"] == 0, "2. an event with no observation data is correctly marked unmeasured")
check("Measured Events" in dax_measures(), "2. DAX defines a Measured Events measure")
check("[Measured Events]" in dax_measures(), "2. Success/Error Rate DAX divides by Measured Events, not Total Events")

# --- 3. Tokens Per Successful Event no longer sums failed-run tokens into the numerator ---
dax = dax_measures()
check("Successful Contributing Tokens" in dax, "3. DAX computes a tokens total FILTERED to successful events")
check(
    "DIVIDE([Successful Contributing Tokens], [Successful Events])" in dax,
    "3. FIXED: Tokens Per Successful Event divides filtered tokens by successful events, not ALL events' tokens",
)

# --- 4. Cache Hit Rate no longer double-counts cache in the denominator for OpenAI-style events ---
ev = normalize(
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
    context=new_trace(trace_id="cache-fix"),
)
cache_row = fact_token_event_rows([ev])[0]
check(cache_row["input_tokens"] == 1000, "4. raw input_tokens is the cache-inclusive 1000 (unchanged, still exported for inspection)")
check(
    cache_row["fresh_input_tokens"] == 600,
    f"4. FIXED: fresh_input_tokens correctly subtracts the cached 400 (got {cache_row['fresh_input_tokens']})",
)
check(
    "[Fresh Input Tokens] + [Cached Input Tokens] + [Cache Creation Tokens]" in dax,
    "4. Cache Hit Rate DAX divides by the FULL prompt input (fresh+cached+cache_creation), "
    "matching analytics/cache.py. Omitting cache_creation overstated the rate for Anthropic-style "
    "providers (cache_creation is a separate additive prompt bucket, not inside input), making the "
    "Power BI dashboard disagree with the analytics/HTML report for the same metric name.",
)

# --- 5. unknown event breakdown stays blank while a real zero stays numeric zero ---
unknown_input = TokenEvent(
    event_id="unknown-input",
    request_correlation_id="r-unknown-input",
    trace_id="t",
    span_id="s",
    quantities=[
        TokenQuantity(
            TokenType.INPUT,
            None,
            PrecisionLevel.UNKNOWN,
            UsageSource.NONE,
            Additivity.TOTAL_CONTRIBUTING,
            unknown_reason=UnknownReason.PROVIDER_OMITTED,
        )
    ],
    observation={"authoritative": True},
)
zero_input = TokenEvent(
    event_id="zero-input",
    request_correlation_id="r-zero-input",
    trace_id="t",
    span_id="s",
    quantities=[q(0)],
    observation={"authoritative": True},
)
breakdown_rows = {row["event_id"]: row for row in fact_token_event_rows([unknown_input, zero_input])}
check(breakdown_rows["unknown-input"]["input_tokens"] is None, "5. unknown input exports as blank, never zero")
check(breakdown_rows["zero-input"]["input_tokens"] == 0, "5. provider-reported zero remains numeric zero")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
