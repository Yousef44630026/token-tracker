"""AWS / Bedrock simulated coverage — Converse streaming, embeddings, robustness.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_aws_simulated.py

SIMULATED but realistic Bedrock shapes: the ConverseStream metadata event, an embeddings
InvokeModel response (header token count), and the no-usage robustness paths.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.bedrock_embeddings_adapter import BedrockEmbeddingsAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

_failures = 0
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic")


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)["response"]


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


# ===== A1. Bedrock ConverseStream: usage arrives in the `metadata` event =====
def converse_text(event):
    block = event.get("contentBlockDelta")
    return block.get("delta", {}).get("text") if block else None


converse_stream = [
    {"messageStart": {"role": "assistant"}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Three orders "}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "were found."}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"messageStop": {"stopReason": "end_turn"}},
    {"metadata": {"usage": {"inputTokens": 1100, "outputTokens": 380, "totalTokens": 1480}, "metrics": {"latencyMs": 1234}}},
]
ev = consume_stream(converse_stream, BedrockConverseAdapter(), context=new_trace(), text_extractor=converse_text)
out = q(ev, TokenType.OUTPUT)
check(ev.provider == "bedrock" and ev.api_surface == "converse", "ConverseStream: provider/surface")
check(out.precision_level == PrecisionLevel.EXACT and out.quantity == 380, "ConverseStream: output EXACT from metadata event (380)")
check(q(ev, TokenType.INPUT).quantity == 1100, "ConverseStream: input from metadata event (1100)")
check(ev.event_contributing_tokens == 1480 and ev.event_total_mismatch == 0, "ConverseStream: 1480, reconciles")

# ===== A3. Bedrock embeddings: header token count -> embedding quantity =====
ev = normalize(load("bedrock_embeddings_full.SIMULATED.json"), BedrockEmbeddingsAdapter(), context=new_trace())
emb = q(ev, TokenType.EMBEDDING)
check(emb is not None and emb.quantity == 640, "Bedrock embeddings: 640 from header")
check(ev.event_contributing_tokens == 640 and ev.event_total_mismatch == 0, "Bedrock embeddings: 640, reconciles")

# ===== A4. Robustness: no usage / no headers -> raw_usage_missing, contributes 0 =====
ev = normalize({"output": {"message": {"role": "assistant"}}}, BedrockConverseAdapter(), context=new_trace())
check("raw_usage_missing" in ev.data_quality_flags and ev.event_contributing_tokens == 0, "Converse without usage -> raw_usage_missing")
ev = normalize(
    {"ResponseMetadata": {"HTTPHeaders": {"content-type": "application/json"}}}, BedrockInvokeModelAdapter(), context=new_trace()
)
check("raw_usage_missing" in ev.data_quality_flags, "InvokeModel without token headers -> raw_usage_missing")

# ===== A2. InvokeModel body variants: headers are source of truth across model families =====
with open(os.path.join(FIX, "bedrock_invoke_model_body_variants.SIMULATED.json"), encoding="utf-8") as f:
    body_variants = json.load(f)["cases"]
for case in body_variants:
    ev = normalize(case["response"], BedrockInvokeModelAdapter(), context=new_trace())
    expected = case["expected_input"] + case["expected_output"]
    check(
        q(ev, TokenType.INPUT).quantity == case["expected_input"]
        and q(ev, TokenType.OUTPUT).quantity == case["expected_output"]
        and ev.event_contributing_tokens == expected,
        f"InvokeModel {case['family']}: header counts survive body-shape drift",
    )

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
