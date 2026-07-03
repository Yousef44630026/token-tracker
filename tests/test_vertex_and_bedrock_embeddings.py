"""Extra — Vertex AI (Gemini wire format) + Bedrock embeddings.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_vertex_and_bedrock_embeddings.py

Vertex AI reuses the Gemini usageMetadata (provider label differs, aliased in the table).
Bedrock embeddings read the input-token-count header into an `embedding` quantity.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_embeddings_adapter import BedrockEmbeddingsAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
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

# ===== Bedrock embeddings: header -> embedding quantity =====
ev = normalize(load("bedrock_embeddings_full.SIMULATED.json"), BedrockEmbeddingsAdapter(), context=new_trace())
emb = q(ev, TokenType.EMBEDDING)
check(emb is not None and emb.quantity == 640, "Bedrock embeddings: 640 from input-token-count header")
check(emb.additivity == Additivity.TOTAL_CONTRIBUTING, "Bedrock embeddings: total_contributing")
check(q(ev, TokenType.INPUT) is None, "Bedrock embeddings: labelled embedding, not input")
check(ev.event_contributing_tokens == 640 and ev.event_total_mismatch == 0, "Bedrock embeddings: 640, reconciles")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
