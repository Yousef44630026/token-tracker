"""GROUND TRUTH — the Azure OpenAI adapter against a REAL captured payload.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_real_payload_azure.py

Mirrors test_real_payload_gemini.py. Runs the adapter on a payload captured from a real Azure
OpenAI Responses call (examples/capture_azure_openai_responses.py). Asserts the
RECONCILIATION property rather than exact counts, so it stays valid if re-captured. Skips
cleanly if the real fixture is absent.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "azure_openai_responses.REAL.json")

if not os.path.exists(REAL):
    print(
        "[SKIP] azure_openai_responses.REAL.json absent - run "
        "examples/capture_azure_openai_responses.py with an Azure OpenAI resource first."
    )
    sys.exit(0)

from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
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
deployment = data.get("_deployment")

ev = normalize(payload, AzureOpenAIResponsesAdapter(deployment=deployment), context=new_trace())


def qty(tt):
    q = next((x for x in ev.quantities if x.token_type == tt), None)
    return q.quantity if q else None


check(ev.provider == "azure_openai", "real payload: provider label is azure_openai")
check(ev.api_surface == "responses", "real payload: api surface is responses")
check(qty(TokenType.INPUT) is not None, "real payload: input_tokens extracted")
check(qty(TokenType.OUTPUT) is not None, "real payload: output_tokens extracted")
check(ev.provider_total_tokens is not None, "real payload: provider total_tokens present")
check(
    all(q.metadata.get("azure_deployment") == deployment for q in ev.quantities),
    "real payload: Azure deployment is preserved separately from response model",
)

# THE ground-truth property: the OpenAI-compatible wire-format assumption holds on real data
check(ev.event_total_mismatch == 0, "GROUND TRUTH: input + output (+ subtotals) reconciles to the real total")
check(ev.event_contributing_tokens == ev.provider_total_tokens, "contributing == provider total on real data")
check("provider_total_mismatch" not in ev.data_quality_flags, "no mismatch flag on the real payload")
check("raw_usage_missing" not in ev.data_quality_flags, "usage was readable on the real Azure response")

print(
    f"\n  real tokens: input={qty(TokenType.INPUT)} output={qty(TokenType.OUTPUT)} "
    f"cached={qty(TokenType.CACHED_INPUT)} reasoning={qty(TokenType.REASONING)} "
    f"total={ev.provider_total_tokens} contributing={ev.event_contributing_tokens}"
)
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
