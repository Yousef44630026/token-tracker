"""Phase 10 — Gemini Generate Content adapter (thinking = total_contributing). (INV-4)

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_gemini_generate_content_adapter.py

SIMULATED fixture (no API credit to capture a real payload). For Gemini, thinking
(thoughtsTokenCount) is total_contributing and added ON TOP of output; cachedContent is a
subtotal_of input (contributes 0). input+output+thinking reconciles to totalTokenCount.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def by_type(usage, tt):
    return next((q for q in usage.quantities if q.token_type == tt), None)


with open(os.path.join(FIXTURES, "gemini_generate_content_thinking.SIMULATED.json"), encoding="utf-8") as f:
    fx = json.load(f)
check(fx.get("_SIMULATED") is True, "fixture is explicitly marked SIMULATED")

usage = GeminiGenerateContentAdapter().extract_usage_from_response(fx["response"])

inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)
cached = by_type(usage, TokenType.CACHED_INPUT)
thinking = by_type(usage, TokenType.THINKING)

check(inp.quantity == 1000 and inp.additivity == Additivity.TOTAL_CONTRIBUTING, "prompt -> input total_contributing")
check(out.quantity == 300 and out.additivity == Additivity.TOTAL_CONTRIBUTING, "candidates -> output total_contributing")
check(
    cached is not None and cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input",
    "cachedContent -> subtotal_of input",
)
check(thinking is not None and thinking.quantity == 250, "thoughts -> thinking extracted")
check(thinking.additivity == Additivity.TOTAL_CONTRIBUTING, "thinking is total_contributing (added on top)")
check(thinking.quantity_in_total == 250, "thinking contributes its tokens")
check(cached.quantity_in_total == 0, "cachedContent contributes 0")

check(usage.provider_total_tokens == 1550, "provider_total_tokens == 1550")

event = TokenEvent(
    event_id="evt-gemini",
    request_correlation_id="r-g",
    trace_id="t-1",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
)
check(event.event_contributing_tokens == 1550, "contributing == input+output+thinking == 1550")
check(event.event_contributing_tokens == event.provider_total_tokens, "reconciles to provider total (no mismatch)")

flags = normalizer_flags(usage.quantities, usage.provider_total_tokens)
check("provider_total_mismatch" not in flags, "no provider_total_mismatch (thinking reconciled)")
check("unverified_additivity" not in flags, "Gemini additivity is verified, not unverified")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
