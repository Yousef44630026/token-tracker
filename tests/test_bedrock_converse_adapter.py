"""Phase 10 — Bedrock Converse adapter (cache = unverified). (INV-4)

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_bedrock_converse_adapter.py

SIMULATED fixture (no API credit to capture a real payload). Bedrock cache fields stay
additivity="unverified": they contribute 0 and raise the normalizer flag unverified_additivity
until verified against a REAL payload. input+output already equal totalTokens (no double count).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
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


with open(os.path.join(FIXTURES, "bedrock_converse_cache.SIMULATED.json"), encoding="utf-8") as f:
    fx = json.load(f)
check(fx.get("_SIMULATED") is True, "fixture is explicitly marked SIMULATED")

usage = BedrockConverseAdapter().extract_usage_from_response(fx["response"])

inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)
cread = by_type(usage, TokenType.CACHED_INPUT)
cwrite = by_type(usage, TokenType.CACHE_CREATION_INPUT)

check(inp.quantity == 1000 and inp.additivity == Additivity.TOTAL_CONTRIBUTING, "input extracted, total_contributing")
check(out.quantity == 300 and out.additivity == Additivity.TOTAL_CONTRIBUTING, "output extracted, total_contributing")
check(cread is not None and cread.additivity == Additivity.UNVERIFIED, "cacheRead -> cached_input UNVERIFIED")
check(cwrite is not None and cwrite.additivity == Additivity.UNVERIFIED, "cacheWrite -> cache_creation_input UNVERIFIED")
check(cread.quantity_in_total == 0 and cwrite.quantity_in_total == 0, "unverified cache fields contribute 0")
check(cread.export_warning == "unverified_additivity_excluded_from_total", "unverified cache is surfaced as a warning")

check(usage.provider_total_tokens == 1300, "provider_total_tokens == 1300")

event = TokenEvent(
    event_id="evt-bedrock",
    request_correlation_id="r-b",
    trace_id="t-1",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
)
check(event.event_contributing_tokens == 1300, "contributing == input+output (cache excluded)")
check(event.event_contributing_tokens == event.provider_total_tokens, "contributing == provider_total (no double count)")

flags = normalizer_flags(usage.quantities, usage.provider_total_tokens)
check("unverified_additivity" in flags, "normalizer raises unverified_additivity")
check("provider_total_mismatch" not in flags, "no provider_total_mismatch (totals reconcile)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
