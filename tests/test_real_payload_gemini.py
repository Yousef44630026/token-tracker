"""GROUND TRUTH — the Gemini adapter against a REAL captured payload.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_real_payload_gemini.py

Unlike the SIMULATED fixtures, this runs the adapter on a payload captured from a real Gemini
call (examples/capture_gemini.py). It asserts the RECONCILIATION property — input + output +
thinking == provider total — rather than the exact counts, so it stays valid if re-captured.
If the real fixture is absent (e.g. on a machine that never ran the capture), it skips cleanly.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "gemini_generate.REAL.json")

if not os.path.exists(REAL):
    print("[SKIP] gemini_generate.REAL.json absent — run examples/capture_gemini.py with a free key first.")
    sys.exit(0)

from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


with open(REAL, encoding="utf-8") as f:
    data = json.load(f)

check(data.get("_SIMULATED") is False, "this is a REAL captured payload (not simulated)")
payload = data["response"]

ev = normalize(payload, GeminiGenerateContentAdapter(), context=new_trace())


def qty(tt):
    q = next((x for x in ev.quantities if x.token_type == tt), None)
    return q.quantity if q else None


# the structure was actually parsed out of the real response
check(qty(TokenType.INPUT) is not None, "real payload: input (promptTokenCount) extracted")
check(qty(TokenType.OUTPUT) is not None, "real payload: output (candidatesTokenCount) extracted")
check(ev.provider_total_tokens is not None, "real payload: provider total (totalTokenCount) present")

# THE ground-truth property: the assumption holds on real data
check(ev.event_total_mismatch == 0, "GROUND TRUTH: input + output + thinking reconciles to the real total")
check(ev.event_contributing_tokens == ev.provider_total_tokens, "contributing == provider total on real data")
check("provider_total_mismatch" not in ev.data_quality_flags, "no mismatch flag on the real payload")
check("raw_usage_missing" not in ev.data_quality_flags, "usage was readable (unknown fields like serviceTier ignored cleanly)")

print(
    f"\n  real tokens: input={qty(TokenType.INPUT)} output={qty(TokenType.OUTPUT)} "
    f"thinking={qty(TokenType.THINKING)} total={ev.provider_total_tokens} contributing={ev.event_contributing_tokens}"
)
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
