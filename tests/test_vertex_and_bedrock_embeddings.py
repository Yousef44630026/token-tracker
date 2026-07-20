"""Extra — Vertex AI (Gemini wire format) + Bedrock embeddings.

Run: python tests/test_vertex_and_bedrock_embeddings.py

Vertex AI reuses the Gemini usageMetadata (provider label differs, aliased in the table).
Bedrock Titan embeddings read the documented body count into an `embedding` quantity.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_embeddings_adapter import BedrockEmbeddingsAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.vertex_ai_embeddings_adapter import VertexAIEmbeddingsAdapter  # noqa: E402
from tracker.adapters.vertex_ai_generate_content_adapter import VertexAIGenerateContentAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

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


# ===== Vertex AI: same Gemini payload, vertex_ai label (aliased) =====
check(issubclass(VertexAIGenerateContentAdapter, GeminiGenerateContentAdapter), "Vertex subclasses Gemini")
ev = normalize(load("vertex_ai_generate_content.SIMULATED.json"), VertexAIGenerateContentAdapter(), context=new_trace())
check(ev.provider == "vertex_ai" and ev.api_surface == "generate_content", "Vertex: provider label")
check(q(ev, TokenType.THINKING).additivity == Additivity.TOTAL_CONTRIBUTING, "Vertex: thinking total_contributing (via gemini alias)")
check(q(ev, TokenType.CACHED_INPUT).quantity_in_total == 0, "Vertex: cached contributes 0 (via alias)")
check(ev.event_contributing_tokens == 2600 and ev.event_total_mismatch == 0, "Vertex: 2600, reconciles")
check(ev.data_quality_flags == [], "Vertex: no unverified flag (alias resolves additivity)")

# ===== Vertex AI embeddings: sum every documented per-embedding token count =====
ev = normalize(
    load("vertex_ai_embeddings.SIMULATED.json"),
    VertexAIEmbeddingsAdapter(model_id="gemini-embedding-001"),
    context=new_trace(),
)
emb = q(ev, TokenType.EMBEDDING)
check(emb is not None and emb.quantity == 14, "Vertex embeddings: 6 + 8 processed tokens")
check(emb.additivity == Additivity.TOTAL_CONTRIBUTING, "Vertex embeddings: token count contributes once")
check(ev.provider_total_tokens is None and ev.event_total_mismatch is None, "Vertex embeddings: no raw provider total is fabricated")
check(ev.model == "gemini-embedding-001", "Vertex embeddings: request model identity is retained")
check(ev.data_quality_flags == [], "Vertex embeddings: complete documented response is clean")

# One missing per-item count must remain a floor plus an explicit unknown, never a false total.
partial = normalize(
    {
        "predictions": [
            {"embeddings": {"statistics": {"token_count": 6, "truncated": False}}},
            {"embeddings": {"statistics": {"truncated": False}}},
        ]
    },
    VertexAIEmbeddingsAdapter(model_id="gemini-embedding-001"),
    context=new_trace(),
)
check(partial.event_contributing_tokens == 6, "Vertex embeddings partial usage keeps the measured floor")
check(partial.provider_total_tokens is None, "Vertex embeddings partial usage never fabricates a provider total")
check(any(item.quantity is None for item in partial.quantities), "Vertex embeddings partial usage carries an UNKNOWN quantity")
check(
    {"provider_usage_missing", "unknown_quantity_present"} <= set(partial.data_quality_flags),
    "Vertex embeddings partial usage is visibly incomplete",
)

truncated = normalize(
    {"embeddings": [{"statistics": {"token_count": 13.0, "truncated": True}}]},
    VertexAIEmbeddingsAdapter(model_id="gemini-embedding-001"),
    context=new_trace(),
)
check(truncated.event_contributing_tokens == 13, "Vertex SDK integral float count is accepted exactly")
check("provider_input_truncated" in truncated.data_quality_flags, "Vertex input truncation is explicit")

# ===== Bedrock Titan embeddings: documented body -> embedding quantity =====
ev = normalize(
    load("bedrock_embeddings_full.SIMULATED.json"),
    BedrockEmbeddingsAdapter(model_id="amazon.titan-embed-text-v2:0"),
    context=new_trace(),
)
emb = q(ev, TokenType.EMBEDDING)
check(emb is not None and emb.quantity == 640, "Bedrock Titan embeddings: 640 from documented body")
check(emb.additivity == Additivity.TOTAL_CONTRIBUTING, "Bedrock embeddings: total_contributing")
check(q(ev, TokenType.INPUT) is None, "Bedrock embeddings: labelled embedding, not input")
check(
    ev.event_contributing_tokens == 640 and ev.provider_total_tokens is None and ev.event_total_mismatch is None,
    "Bedrock embeddings: exact quantity without fabricated provider total",
)

legacy = normalize(
    {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "77"}}},
    BedrockEmbeddingsAdapter(model_id="cohere.embed-english-v3"),
    context=new_trace(),
)
check(legacy.event_contributing_tokens == 0, "non-contractual embedding header fails closed")
check(
    {"provider_usage_unverified", "unverified_additivity"} <= set(legacy.data_quality_flags),
    "non-contractual embedding header is audit-visible",
)

cohere = normalize(
    {"body_json": {"embeddings": [[0.1, 0.2]]}},
    BedrockEmbeddingsAdapter(model_id="cohere.embed-english-v3"),
    context=new_trace(),
)
check(
    cohere.event_contributing_tokens == 0 and "raw_usage_missing" in cohere.data_quality_flags,
    "Cohere Embed response cannot fabricate a token count it does not expose",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
