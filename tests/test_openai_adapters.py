"""Phase 5 — OpenAI adapters (Responses + Chat Completions). (INV-4 / no double count)

Run: python tests/test_openai_adapters.py

NOTE: fixtures are SIMULATED (marked _SIMULATED), built to the documented OpenAI usage shape
because no API credit is available to capture a real payload. They exercise the real
adapter/additivity logic; swap in a recorded payload to make this a ground-truth test.

For a cached+reasoning response on BOTH surfaces:
  - input, output      = total_contributing
  - cached_input       = subtotal_of "input"   (contributes 0)
  - reasoning          = subtotal_of "output"  (contributes 0)
  => event_contributing_tokens == provider_total_tokens (no double count).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def by_type(usage, tt):
    return next((q for q in usage.quantities if q.token_type == tt), None)


def assert_surface(label, adapter, fixture_file):
    fx = load(fixture_file)
    check(fx.get("_SIMULATED") is True, f"{label}: fixture is explicitly marked SIMULATED")
    usage = adapter.extract_usage_from_response(fx["response"])

    inp = by_type(usage, TokenType.INPUT)
    out = by_type(usage, TokenType.OUTPUT)
    cached = by_type(usage, TokenType.CACHED_INPUT)
    reasoning = by_type(usage, TokenType.REASONING)

    check(inp is not None and inp.quantity == 1000, f"{label}: input extracted (1000)")
    check(out is not None and out.quantity == 300, f"{label}: output extracted (300)")
    check(cached is not None and cached.quantity == 800, f"{label}: cached_input extracted (800)")
    check(reasoning is not None and reasoning.quantity == 250, f"{label}: reasoning extracted (250)")

    check(inp.precision_level == PrecisionLevel.EXACT, f"{label}: input is EXACT")
    check(inp.additivity == Additivity.TOTAL_CONTRIBUTING, f"{label}: input total_contributing")
    check(out.additivity == Additivity.TOTAL_CONTRIBUTING, f"{label}: output total_contributing")
    check(cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input", f"{label}: cached_input subtotal_of input")
    check(reasoning.additivity == Additivity.SUBTOTAL_OF and reasoning.subtotal_of == "output", f"{label}: reasoning subtotal_of output")

    check(usage.provider_total_tokens == 1300, f"{label}: provider_total_tokens == 1300")
    check(cached.quantity_in_total == 0 and reasoning.quantity_in_total == 0, f"{label}: subtotals contribute 0")

    event = TokenEvent(
        event_id=f"evt-{label}",
        request_correlation_id=f"r-{label}",
        trace_id="t-1",
        span_id="s-1",
        provider=usage.provider,
        api_surface=usage.api_surface,
        model=usage.model,
        quantities=usage.quantities,
        provider_total_tokens=usage.provider_total_tokens,
        observation={"authoritative": True},
    )
    check(event.event_contributing_tokens == 1300, f"{label}: event_contributing_tokens == 1300")
    check(event.event_contributing_tokens == event.provider_total_tokens, f"{label}: contributing == provider_total (no double count)")
    check(event.event_total_mismatch == 0, f"{label}: no provider/derived mismatch")


assert_surface("responses", OpenAIResponsesAdapter(), "openai_responses_cached_reasoning.SIMULATED.json")
assert_surface("chat", OpenAIChatCompletionsAdapter(), "openai_chat_completions_cached_reasoning.SIMULATED.json")

# --- raw_usage_missing: an empty response is flagged, not crashed ---
empty = OpenAIResponsesAdapter().extract_usage_from_response({"id": "x"})
check("raw_usage_missing" in empty.data_quality_flags, "missing usage object -> raw_usage_missing flag")
check(empty.quantities == [], "missing usage object -> no fabricated quantities")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
