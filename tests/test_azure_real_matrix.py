"""GROUND TRUTH — the Azure confrontation matrix, locked as permanent regression.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_azure_real_matrix.py

Loads the REAL Azure payloads captured by examples/azure_matrix_family_a.py and
examples/azure_matrix_family_b.py (gpt-5-mini) and asserts, on real data, the two pillars the
simulated fixtures could only assume:

  - ADDITIVITY (Family A): cached_input is a subtotal of input, reasoning a subtotal of output;
    both contribute 0, so contributing == the provider total — no double counting of cache or
    reasoning, even when both are present (A5).
  - SUPERSESSION (Family B): a streamed final usage reconciles exactly (B1); an interrupted
    partial estimate is superseded by the real final usage and contributes 0, so the trace
    counts the final only (B4) — no double counting of a streamed retry.

Reconciliation (not exact counts) is asserted, so the test stays valid if re-captured. Each
fixture is skipped cleanly if absent, so the suite still runs on a machine without them; the
fixtures are committed, so CI does exercise them.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

check = make_checker()
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic")


def load(name: str):
    path = os.path.join(FIX, f"{name}.REAL.json")
    if not os.path.exists(path):
        print(f"[SKIP] {name}.REAL.json absent — run the matrix runner to capture it.")
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    check(data.get("_SIMULATED") is False, f"{name}: is a REAL captured payload (not simulated)")
    return data


def chat_event(data):
    return normalize(data["response"], AzureOpenAIChatCompletionsAdapter(deployment=data.get("_deployment")), context=new_trace())


def qty(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


def reconciles(ev) -> bool:
    return ev.event_total_mismatch == 0 and ev.event_contributing_tokens == ev.provider_total_tokens


# --- A1 / A9 — plain chat + truncation: exact, reconciles -----------------------------------
for name, label in [("azure_A1_simple", "A1 simple"), ("azure_A9_truncated", "A9 truncated")]:
    data = load(name)
    if data:
        ev = chat_event(data)
        inp, out = qty(ev, TokenType.INPUT), qty(ev, TokenType.OUTPUT)
        check(inp is not None and out is not None, f"{label}: input+output extracted from real payload")
        check(out.precision_level == PrecisionLevel.EXACT, f"{label}: output is EXACT (truncation is not a quality defect)")
        check(reconciles(ev), f"{label}: GROUND TRUTH — input+output reconciles to the real provider total")

# --- A2 (cache miss) / A3 (cache hit) — cached_input is a subtotal, still reconciles ---------
miss = load("azure_A2_cache_call1")
if miss:
    ev = chat_event(miss)
    check(reconciles(ev), "A2 cache-miss: reconciles")

hit = load("azure_A3_cache_call2")
if hit:
    ev = chat_event(hit)
    cached = qty(ev, TokenType.CACHED_INPUT)
    check(cached is not None and cached.quantity > 0, "A3 cache-hit: cached_input present and > 0 on real data")
    check(cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input", "A3: cached_input is a SUBTOTAL of input")
    check(cached.quantity_in_total == 0, "A3: the cached subtotal contributes 0 to the total")
    check(reconciles(ev), "A3 GROUND TRUTH: cache is not double-counted — contributing == provider total")

# --- A4 — reasoning is a subtotal of output, contributes 0, reconciles ----------------------
data = load("azure_A4_reasoning")
if data:
    ev = chat_event(data)
    reasoning = qty(ev, TokenType.REASONING)
    check(reasoning is not None and reasoning.quantity > 0, "A4: reasoning tokens present on real o-series/gpt-5 data")
    check(reasoning.additivity == Additivity.SUBTOTAL_OF and reasoning.subtotal_of == "output", "A4: reasoning is a SUBTOTAL of output")
    check(reasoning.quantity_in_total == 0, "A4: the reasoning subtotal contributes 0")
    forbidden = {"partial_output_observed", "estimated_input", "estimated_output"}
    check(all(q.token_type.value not in forbidden for q in ev.quantities), "A4: token_type purity — no forbidden measurement-as-type")
    check(reconciles(ev), "A4 GROUND TRUTH: reasoning is not double-counted — contributing == provider total")

# --- A5 — cache AND reasoning together: both subtotals, sum still == provider total ----------
data = load("azure_A5_cache_plus_reasoning")
if data:
    ev = chat_event(data)
    cached, reasoning = qty(ev, TokenType.CACHED_INPUT), qty(ev, TokenType.REASONING)
    check(cached is not None and cached.quantity > 0, "A5: cached_input present")
    check(reasoning is not None and reasoning.quantity > 0, "A5: reasoning present")
    check(cached.quantity_in_total == 0 and reasoning.quantity_in_total == 0, "A5: both subtotals contribute 0")
    check(reconciles(ev), "A5 GROUND TRUTH: cache + reasoning TOGETHER still reconcile — no double count (the Phase-5 falsifier, real)")

# --- A6 — embeddings: the embedded tokens ARE the billable total ----------------------------
data = load("azure_A6_embeddings")
if data:
    ev = normalize(data["response"], AzureOpenAIEmbeddingsAdapter(deployment=data.get("_deployment")), context=new_trace())
    emb = qty(ev, TokenType.EMBEDDING)
    check(emb is not None and emb.precision_level == PrecisionLevel.EXACT, "A6: embedding tokens extracted, EXACT")
    check(emb.additivity == Additivity.TOTAL_CONTRIBUTING, "A6: embedding tokens are total_contributing")
    check(reconciles(ev), "A6 GROUND TRUTH: embedding tokens reconcile to the provider total")

# --- A7 — vision: image tokens fold into input; NO fabricated image_input quantity ----------
data = load("azure_A7_vision")
if data:
    ev = chat_event(data)
    check(qty(ev, TokenType.INPUT) is not None, "A7: input tokens (image folded in) extracted")
    check(qty(ev, TokenType.IMAGE_INPUT) is None, "A7: no per-modality breakdown reported -> no fabricated image_input (INV-6)")
    check(reconciles(ev), "A7 GROUND TRUTH: vision call reconciles")

# --- B1 — completed stream + include_usage: exact, provider_stream_final, reconciles ---------
data = load("azure_B1_stream_complete")
if data:
    chunks = data["captured"]
    usage_chunk = next((c for c in chunks if isinstance(c, dict) and c.get("usage")), None)
    check(usage_chunk is not None, "B1: a final usage chunk was captured (include_usage)")
    if usage_chunk:
        adapter = AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini")
        usage = adapter.extract_usage_from_stream_event(usage_chunk)
        tr = StreamTracker.from_context(new_trace(), provider="azure_openai", api_surface="chat_completions", model=usage.model)
        ev = tr.complete_with_quantities(quantities=usage.quantities, provider_total_tokens=usage.provider_total_tokens, model=usage.model)
        out = qty(ev, TokenType.OUTPUT)
        check(out.precision_level == PrecisionLevel.EXACT, "B1: streamed final usage is EXACT")
        check(out.usage_source.value == "provider_stream_final", "B1: provenance is provider_stream_final (not a response body)")
        check(reconciles(ev), "B1 GROUND TRUTH: streamed usage reconciles to the provider total")

# --- B4 — interrupted partial superseded by the real final usage: trace counts final only ---
data = load("azure_B4_final_usage")
if data:
    u = data["captured"]["usage"]
    tr = StreamTracker.from_context(new_trace(), provider="azure_openai", api_surface="chat_completions", model="gpt-5-mini")
    tr.feed("red\ngre")  # a partial stream the client saw before the cut
    partial = tr.interrupt()
    check("stream_interrupted" in partial.data_quality_flags, "B4: the partial is flagged stream_interrupted")
    check(partial.quantities[-1].precision_level == PrecisionLevel.ESTIMATE, "B4: the partial output is an ESTIMATE, not exact")
    final = tr.resolve_with_final_usage(
        output_tokens=u["completion_tokens"], input_tokens=u["prompt_tokens"], provider_total_tokens=u["total_tokens"]
    )
    trace = Trace(trace_id=final.trace_id, events=[partial, final])
    total = observed_total_contributing_tokens(trace)
    check(partial.superseded and partial.superseded_by == final.event_id, "B4: the partial is superseded by the final (correlated)")
    check(partial.event_contributing_tokens == 0, "B4: the superseded partial contributes 0")
    check(
        total == final.event_contributing_tokens == u["total_tokens"],
        "B4 GROUND TRUTH: the trace counts the FINAL only — partial+final never double-counted (real magnitudes)",
    )

sys.exit(check.report("RESULT test_azure_real_matrix"))
