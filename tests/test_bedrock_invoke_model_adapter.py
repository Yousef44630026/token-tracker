"""Bedrock InvokeModel: documented body contracts and fail-closed legacy evidence.

Run: python tests/test_bedrock_invoke_model_adapter.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, Trust  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def by_type(usage, token_type):
    return next((quantity for quantity in usage.quantities if quantity.token_type == token_type), None)


# Non-contractual headers remain measured evidence, but are never trusted for addition.
with open(os.path.join(FIXTURES, "bedrock_invoke_model_headers.SIMULATED.json"), encoding="utf-8") as handle:
    header_payload = json.load(handle)["response"]

usage = BedrockInvokeModelAdapter().extract_usage_from_response(header_payload)
inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)
check(inp.quantity == 1000 and out.quantity == 300, "legacy header observations are retained")
check(
    inp.precision_level == PrecisionLevel.EXACT
    and inp.additivity == Additivity.UNVERIFIED
    and inp.trust == Trust.UNVERIFIED,
    "legacy header is exactly observed but accounting-unverified",
)
check("provider_usage_unverified" in usage.data_quality_flags, "legacy source is explicitly flagged")
event = normalize(header_payload, BedrockInvokeModelAdapter(), context=new_trace())
check(event.event_contributing_tokens == 0, "legacy headers cannot inflate the canonical total")
check(
    {"provider_usage_unverified", "unverified_additivity"} <= set(event.data_quality_flags),
    "legacy event exposes both source and additivity risk",
)

# Header lookup remains case-insensitive, without upgrading trust.
upper = {
    "ResponseMetadata": {
        "HTTPHeaders": {
            "X-Amzn-Bedrock-Input-Token-Count": "50",
            "X-Amzn-Bedrock-Output-Token-Count": "7",
        }
    }
}
upper_usage = BedrockInvokeModelAdapter().extract_usage_from_response(upper)
check(
    by_type(upper_usage, TokenType.INPUT).quantity == 50
    and by_type(upper_usage, TokenType.OUTPUT).quantity == 7,
    "legacy header keys are matched case-insensitively",
)

# Documented model-family bodies are the exact path.
variant_path = os.path.join(FIXTURES, "realistic", "bedrock_invoke_model_body_variants.SIMULATED.json")
with open(variant_path, encoding="utf-8") as handle:
    variants = json.load(handle)["cases"]

for case in variants:
    adapter = BedrockInvokeModelAdapter(model_id=case["model_id"])
    usage = adapter.extract_usage_from_response(case["response"])
    inp = by_type(usage, TokenType.INPUT)
    out = by_type(usage, TokenType.OUTPUT)
    check(
        inp is not None
        and out is not None
        and inp.quantity == case["expected_input"]
        and out.quantity == case["expected_output"],
        f"{case['family']}: observed counts are retained",
    )
    event = normalize(case["response"], adapter, context=new_trace())
    expected = case["expected_input"] + case["expected_output"]
    if case["expected_verified"]:
        check(event.event_contributing_tokens == expected, f"{case['family']}: documented body contributes exactly")
        check("unverified_additivity" not in event.data_quality_flags, f"{case['family']}: documented body is trusted")
    else:
        check(event.event_contributing_tokens == 0, f"{case['family']}: unsupported body fails closed")
        check("provider_usage_unverified" in event.data_quality_flags, f"{case['family']}: unsupported source is flagged")
    check(
        event.provider_total_tokens == case["expected_provider_total"],
        f"{case['family']}: provider total is raw-only",
    )

# A boto3 StreamingBody-like object must never be consumed by normalization.
class ReadTrap:
    def read(self):
        raise AssertionError("adapter consumed the response stream")


unread = normalize({"body": ReadTrap()}, BedrockInvokeModelAdapter(), context=new_trace())
check("raw_usage_missing" in unread.data_quality_flags, "undecoded body fails closed without being consumed")

# Ordinary InvokeModelWithResponseStream chunks have no documented terminal usage contract.
check(
    BedrockInvokeModelAdapter().extract_usage_from_stream_event(header_payload) is None,
    "stream envelope headers are not promoted to a terminal usage event",
)

# A Titan body without the output counter is a measured floor with an explicit missing flag.
partial = normalize(
    {"body_json": {"inputTextTokenCount": 12}},
    BedrockInvokeModelAdapter(model_id="amazon.titan-text-premier-v1:0"),
    context=new_trace(),
)
check(partial.event_contributing_tokens == 12, "partial Titan usage preserves the measured floor")
check("provider_usage_missing" in partial.data_quality_flags, "partial Titan usage is visibly incomplete")

# Sanity: the canonical event model still sees no raw total for Titan.
full = normalize(
    {"body_json": {"inputTextTokenCount": 10, "results": [{"tokenCount": 3}]}},
    BedrockInvokeModelAdapter(model_id="amazon.titan-text-premier-v1:0"),
    context=new_trace(),
)
check(
    isinstance(full, TokenEvent) and full.event_contributing_tokens == 13 and full.event_total_mismatch is None,
    "Titan exact quantities do not fabricate an event-level provider total",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
