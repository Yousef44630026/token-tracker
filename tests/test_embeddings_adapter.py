"""Extra — Embeddings adapters (the RAG token source that was missing).

Run: python tests/test_embeddings_adapter.py

An embeddings call has no output: it produces a single EMBEDDING quantity (total_contributing)
from usage.prompt_tokens, reconciling to total_tokens. Verified on a realistic full payload
(a 3-input batch). Azure reuses the same shape with its own provider label.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.adapters.openai_embeddings_adapter import OpenAIEmbeddingsAdapter  # noqa: E402
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


def load_fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


payload = load("openai_embeddings_full.SIMULATED.json")

# --- OpenAI embeddings ---
ev = normalize(payload, OpenAIEmbeddingsAdapter(), context=new_trace())
emb = q(ev, TokenType.EMBEDDING)
check(emb is not None and emb.quantity == 192, "embedding tokens extracted from usage.prompt_tokens (192)")
check(emb.additivity == Additivity.TOTAL_CONTRIBUTING, "embedding is total_contributing (the real cost)")
check(q(ev, TokenType.INPUT) is None and q(ev, TokenType.OUTPUT) is None, "no input/output quantity for an embeddings call")
check(ev.event_contributing_tokens == 192, "embeddings call contributes 192")
check(ev.provider_total_tokens == 192 and ev.event_total_mismatch == 0, "reconciles to total_tokens")
check(ev.model == "text-embedding-3-small" and ev.api_surface == "embeddings", "model + surface set")
check(ev.data_quality_flags == [], "no unverified flag (embedding is registered, not fail-closed)")

# --- Azure embeddings: same shape, azure label ---
check(issubclass(AzureOpenAIEmbeddingsAdapter, OpenAIEmbeddingsAdapter), "Azure embeddings subclasses OpenAI embeddings")
az_fixture = load_fixture("azure_openai_embeddings.SIMULATED.json")
az = normalize(
    az_fixture["response"],
    AzureOpenAIEmbeddingsAdapter(deployment=az_fixture["_deployment"]),
    context=new_trace(),
)
check(az.provider == "azure_openai" and az.event_contributing_tokens == 192, "Azure embeddings -> azure_openai, 192")
check(az.model == "text-embedding-3-small", "Azure embeddings preserves response model")
check(q(az, TokenType.EMBEDDING).metadata.get("azure_deployment") == "embed-prod", "Azure embeddings stores deployment separately")

# --- missing usage / not streamed ---
empty = normalize({"object": "list", "data": []}, OpenAIEmbeddingsAdapter(), context=new_trace())
check("raw_usage_missing" in empty.data_quality_flags and empty.event_contributing_tokens == 0, "no usage -> raw_usage_missing, 0")
check(OpenAIEmbeddingsAdapter().extract_usage_from_stream_event({"data": []}) is None, "embeddings are not streamed -> None")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
