"""Extra — shared event assembly (event_builder.build_event / deduplicate_flags).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_event_builder.py

build_event is the single place both the normalizer and the stream tracker assemble an event:
it wires identity from the context, applies the normalizer-owned flags exactly once, and
merges leading/trailing flags de-duplicated. event_id is generated unless supplied.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.event_builder import build_event, deduplicate_flags  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(qty, add=Additivity.TOTAL_CONTRIBUTING, parent=None):
    return TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, add, subtotal_of=parent)


# --- deduplicate_flags: order preserved, dups and empties removed ---
check(deduplicate_flags(["a", "", "b", "a", "c", "", "b"]) == ["a", "b", "c"], "dedup keeps order, drops dups/empties")
check(deduplicate_flags([]) == [], "dedup of empty -> empty")

ctx = new_trace(business_id="biz", workflow="wf", environment="prod")

# --- identity wiring + generated event_id ---
ev = build_event(
    context=ctx, provider="openai", api_surface="chat_completions", model="gpt-4o", quantities=[q(100)], provider_total_tokens=100
)
check(ev.trace_id == ctx.trace_id and ev.span_id == ctx.span_id, "identity wired from context")
check(ev.request_correlation_id == ctx.request_correlation_id, "request_correlation_id wired")
check(ev.business_id == "biz" and ev.workflow == "wf" and ev.environment == "prod", "labels wired")
check(ev.provider == "openai" and ev.model == "gpt-4o", "provider fields set")
check(ev.event_id.startswith("evt-"), "event_id generated when not supplied")
check(ev.data_quality_flags == [], "clean event -> no flags")

# --- supplied event_id honored ---
ev2 = build_event(
    context=ctx, provider=None, api_surface=None, model=None, quantities=[q(100)], provider_total_tokens=100, event_id="fixed-id"
)
check(ev2.event_id == "fixed-id", "supplied event_id is used")

# --- normalizer flags applied; leading/trailing merged + de-duplicated ---
ev3 = build_event(
    context=ctx,
    provider="bedrock",
    api_surface="converse",
    model=None,
    quantities=[q(100), q(80, Additivity.UNVERIFIED)],
    provider_total_tokens=100,
    leading_flags=["raw_usage_missing", "unverified_additivity"],
    trailing_flags=["custom", "custom"],
)
check("unverified_additivity" in ev3.data_quality_flags, "normalizer flag (unverified) applied")
check(ev3.data_quality_flags.count("unverified_additivity") == 1, "leading flag not duplicated with the normalizer one")
check("raw_usage_missing" in ev3.data_quality_flags and "custom" in ev3.data_quality_flags, "leading + trailing flags merged")
check(ev3.data_quality_flags.count("custom") == 1, "trailing duplicates collapsed")

# --- provider/derived mismatch is detected by the shared builder ---
ev4 = build_event(
    context=ctx, provider="openai", api_surface="chat_completions", model=None, quantities=[q(100)], provider_total_tokens=999
)
check("provider_total_mismatch" in ev4.data_quality_flags, "mismatch flagged by build_event")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
