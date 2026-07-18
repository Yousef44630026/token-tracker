"""Extra — Bedrock InvokeModel adapter (token counts from Bedrock HTTP headers).

Run: python tests/test_bedrock_invoke_model_adapter.py

SIMULATED fixture. InvokeModel bodies are model-specific, but Bedrock returns model-agnostic
token counts in the response headers (x-amzn-bedrock-input/output-token-count). The adapter
reads those (case-insensitively). No total is provided -> provider_total_tokens is None.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
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


with open(os.path.join(FIXTURES, "bedrock_invoke_model_headers.SIMULATED.json"), encoding="utf-8") as f:
    payload = json.load(f)["response"]

usage = BedrockInvokeModelAdapter().extract_usage_from_response(payload)
inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)

check(
    inp is not None and inp.quantity == 1000 and inp.additivity == Additivity.TOTAL_CONTRIBUTING,
    "input from header (1000), total_contributing",
)
check(
    out is not None and out.quantity == 300 and out.additivity == Additivity.TOTAL_CONTRIBUTING,
    "output from header (300), total_contributing",
)
check(usage.provider == "bedrock" and usage.api_surface == "invoke_model", "provider/surface set")
check(usage.provider_total_tokens is None, "no total from InvokeModel headers -> None")

event = TokenEvent(
    event_id="evt-bim",
    request_correlation_id="r",
    trace_id="t",
    span_id="s",
    provider=usage.provider,
    api_surface=usage.api_surface,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
    observation={"authoritative": True},
)
check(event.event_contributing_tokens == 1300, "contributing == input+output == 1300")
check(event.event_total_mismatch is None, "no provider total -> mismatch None")

# --- header lookup is case-insensitive (boto3 may vary casing) ---
upper = {"ResponseMetadata": {"HTTPHeaders": {"X-Amzn-Bedrock-Input-Token-Count": "50", "X-Amzn-Bedrock-Output-Token-Count": "7"}}}
u2 = BedrockInvokeModelAdapter().extract_usage_from_response(upper)
check(by_type(u2, TokenType.INPUT).quantity == 50 and by_type(u2, TokenType.OUTPUT).quantity == 7, "header keys matched case-insensitively")

# --- no headers -> raw_usage_missing, no fabricated quantities ---
empty = BedrockInvokeModelAdapter().extract_usage_from_response({"contentType": "application/json"})
check("raw_usage_missing" in empty.data_quality_flags and empty.quantities == [], "no token headers -> raw_usage_missing")

# --- through the keystone ---
ev = normalize(payload, BedrockInvokeModelAdapter(), context=new_trace())
check(
    ev.provider == "bedrock" and ev.api_surface == "invoke_model" and ev.event_contributing_tokens == 1300,
    "normalize() yields a bedrock invoke_model event (1300)",
)

# --- A2: model-specific InvokeModel bodies must not become source-of-truth token counts ---
with open(os.path.join(FIXTURES, "realistic", "bedrock_invoke_model_body_variants.SIMULATED.json"), encoding="utf-8") as f:
    variants = json.load(f)["cases"]

for case in variants:
    usage = BedrockInvokeModelAdapter().extract_usage_from_response(case["response"])
    inp = by_type(usage, TokenType.INPUT)
    out = by_type(usage, TokenType.OUTPUT)
    expected_total = case["expected_input"] + case["expected_output"]
    check(
        inp is not None and out is not None and inp.quantity == case["expected_input"] and out.quantity == case["expected_output"],
        f"A2 {case['family']}: headers win over model-specific body token fields",
    )
    check(
        usage.provider_total_tokens is None
        and TokenEvent(
            event_id=f"evt-{case['family']}",
            request_correlation_id=f"req-{case['family']}",
            trace_id="t",
            span_id=f"s-{case['family']}",
            provider=usage.provider,
            api_surface=usage.api_surface,
            quantities=usage.quantities,
            provider_total_tokens=usage.provider_total_tokens,
            observation={"authoritative": True},
        ).event_contributing_tokens
        == expected_total,
        f"A2 {case['family']}: contributes header input+output only",
    )

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
