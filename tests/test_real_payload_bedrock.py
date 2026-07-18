"""GROUND TRUTH — the Bedrock Converse adapter against a REAL captured payload.

Run: python tests/test_real_payload_bedrock.py

Mirrors test_real_payload_gemini.py / test_real_payload_azure.py. Runs the adapter on a
payload captured from a real AWS Bedrock Converse call (examples/capture_bedrock_converse.py).
Asserts the RECONCILIATION property rather than exact counts, so it stays valid if
re-captured. Skips cleanly if the real fixture is absent.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "bedrock_converse.REAL.json")

if not os.path.exists(REAL):
    print("[SKIP] bedrock_converse.REAL.json absent — run examples/capture_bedrock_converse.py with an AWS account first.")
    sys.exit(0)

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
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

ev = normalize(payload, BedrockConverseAdapter(), context=new_trace())


def qty(tt):
    q = next((x for x in ev.quantities if x.token_type == tt), None)
    return q.quantity if q else None


check(ev.provider == "bedrock" and ev.api_surface == "converse", "real payload: provider label is bedrock/converse")
check(qty(TokenType.INPUT) is not None, "real payload: input (inputTokens) extracted")
check(qty(TokenType.OUTPUT) is not None, "real payload: output (outputTokens) extracted")
check(ev.provider_total_tokens is not None, "real payload: provider total (totalTokens) present")

# THE ground-truth property: input + output reconciles to the real total (cache fields, if
# present, stay unverified/excluded until separately confirmed — see the capture script note)
check(ev.event_total_mismatch == 0, "GROUND TRUTH: input + output reconciles to the real total")
check(ev.event_contributing_tokens == ev.provider_total_tokens, "contributing == provider total on real data")
check("raw_usage_missing" not in ev.data_quality_flags, "usage was readable on the real Bedrock response")

print(
    f"\n  real tokens: input={qty(TokenType.INPUT)} output={qty(TokenType.OUTPUT)} "
    f"cached={qty(TokenType.CACHED_INPUT)} total={ev.provider_total_tokens} contributing={ev.event_contributing_tokens}"
)
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
