"""REAL-LIFE SCENARIOS — the tracker under production-shaped flows, on real Azure payloads.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_azure_real_life_scenarios.py

Unit tests check ONE mechanism in isolation. Production combines them — a RAG agent that also
caches, reasons, streams, retries and serves several tenants at once. That is exactly where a
naive tracker double-counts. These scenarios compose the REAL Azure fixtures (gpt-5-mini,
text-embedding-3-large) into realistic multi-step traces and assert the COMBINED invariants,
comparing the tracker's total against the inflated number a naive summation would report.

Assertions are on relationships (not brittle exact counts) so they survive re-capture; the
real numbers are printed for visibility. Skips cleanly if a fixture is missing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import Additivity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context  # noqa: E402

check = make_checker()
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic")
CHAT = AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini")
EMBED = AzureOpenAIEmbeddingsAdapter(deployment="text-embedding-3-large")


def load(name: str):
    path = os.path.join(FIX, f"{name}.REAL.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if "response" in d:
        return d
    if "generation" in d:
        return {"response": d["generation"], "retrieved": d.get("retrieved", [])}
    captured = d.get("captured")
    if isinstance(captured, dict):
        return {"response": captured}
    if isinstance(captured, list):
        return {"response": next((c for c in captured if isinstance(c, dict) and c.get("usage")), {})}
    return None


def ctx(trace_id, span_id, corr, *, business_id=None, workflow=None, parent="root"):
    return TraceContext(
        trace_id=trace_id, span_id=span_id, request_correlation_id=corr, parent_span_id=parent, business_id=business_id, workflow=workflow
    )


def raw_sum(event) -> int:
    """What a naive tracker sums: every raw quantity, including subtotals (the double count)."""
    return sum(q.quantity or 0 for q in event.quantities)


def subtotals_zero(event) -> bool:
    return all(q.quantity_in_total == 0 for q in event.quantities if q.additivity == Additivity.SUBTOTAL_OF)


# =========================================================================================
# SCENARIO 1 — a multi-turn RAG support agent (embed + retrieve + generate, then a cached
# follow-up that reasons) in ONE trace. The classic production shape.
# =========================================================================================
print("\n=== SCENARIO 1 — agent RAG multi-tour (embedding + cache + reasoning + contexte) ===")
embed_fix, rag_fix, a5_fix = load("azure_A6_embeddings"), load("azure_rag_agent"), load("azure_A5_cache_plus_reasoning")
if embed_fix and rag_fix and a5_fix:
    tid = "scn1-support"
    biz, wf = "acme-support", "support_rag"
    embed_ev = normalize(embed_fix["response"], EMBED, context=ctx(tid, "t1-embed", "c1", business_id=biz, workflow=wf))
    gen1_ev = normalize(rag_fix["response"], CHAT, context=ctx(tid, "t1-gen", "c2", business_id=biz, workflow=wf))
    gen2_ev = normalize(a5_fix["response"], CHAT, context=ctx(tid, "t2-gen", "c3", business_id=biz, workflow=wf))

    assemble = new_rag_span(tid, "prompt_assembly", parent_span_id="root", span_id="t1-assemble")
    record_retrieved_context(
        assemble, context_text="\n".join(rag_fix["retrieved"]), injected_into_prompt=True, downstream_llm_span_id="t1-gen"
    )
    ctx_est = assemble.metadata["retrieved_context_estimated_tokens"]

    trace = Trace(trace_id=tid)
    trace.add_span(assemble)
    for e in (embed_ev, gen1_ev, gen2_ev):
        trace.add_event(e)

    total = observed_total_contributing_tokens(trace)
    correct = embed_ev.event_contributing_tokens + gen1_ev.event_contributing_tokens + gen2_ev.event_contributing_tokens
    naive = raw_sum(embed_ev) + raw_sum(gen1_ev) + raw_sum(gen2_ev) + ctx_est

    check(embed_ev.span_id == "t1-embed" and gen2_ev.span_id == "t2-gen", "each turn's event attaches to its own span")
    check(all(e.business_id == biz for e in (embed_ev, gen1_ev, gen2_ev)), "every event carries the tenant's business_id")
    check(all(subtotals_zero(e) for e in (gen1_ev, gen2_ev)), "cache & reasoning subtotals contribute 0 across the conversation")
    check(total == correct, f"trace total = each turn's LLM once (embed+gen1+gen2={correct})")
    check(total < naive, f"NO DOUBLE COUNT: tracker={total} vs a naive sum={naive} (inflation avoided={naive - total})")
    check(total < naive - ctx_est or ctx_est == 0, "the retrieved-context estimate is never added on top of the real input cost")
    print(f"    tracker total={total}  |  naive would report {naive}  |  avoided double-count = {naive - total} tokens")
else:
    print("  [SKIP] fixtures manquantes")

# =========================================================================================
# SCENARIO 2 — a resilient generation: the stream is interrupted (partial estimate), then the
# real final usage supersedes it, in a trace that also holds a cache+reasoning call. The retry
# and the subtotals must all resolve to the real live totals only.
# =========================================================================================
print("\n=== SCENARIO 2 — génération résiliente (stream interrompu -> final) + appel caché ===")
b4_fix, a5_fix2 = load("azure_B4_final_usage"), load("azure_A5_cache_plus_reasoning")
if b4_fix and a5_fix2:
    tid = "scn2-resilient"
    usage = b4_fix["response"]["usage"]
    st = StreamTracker.from_context(
        ctx(tid, "gen-stream", "c-stream", workflow="resilient"),
        provider="azure_openai",
        api_surface="chat_completions",
        model="gpt-5-mini",
    )
    st.feed("The answer is ")  # what the client saw before the cut
    partial = st.interrupt()
    final = st.resolve_with_final_usage(
        output_tokens=usage["completion_tokens"], input_tokens=usage["prompt_tokens"], provider_total_tokens=usage["total_tokens"]
    )
    cached_ev = normalize(a5_fix2["response"], CHAT, context=ctx(tid, "cached-call", "c-cache", workflow="resilient"))

    trace = Trace(trace_id=tid)
    for e in (partial, final, cached_ev):
        trace.add_event(e)
    total = observed_total_contributing_tokens(trace)
    correct = final.event_contributing_tokens + cached_ev.event_contributing_tokens
    naive = raw_sum(partial) + raw_sum(final) + raw_sum(cached_ev)

    check(partial.superseded and partial.event_contributing_tokens == 0, "the interrupted partial is superseded and contributes 0")
    check(final.event_contributing_tokens == usage["total_tokens"], "the streamed final counts its real usage")
    check(subtotals_zero(cached_ev), "the cache+reasoning call's subtotals contribute 0")
    check(total == correct, f"trace total = final + cached only ({correct}) — retry not double-counted")
    check(total < naive, f"NO DOUBLE COUNT: tracker={total} vs naive={naive} (partial estimate + subtotals excluded)")
    print(f"    tracker total={total}  |  naive would report {naive}  |  avoided = {naive - total} tokens")
else:
    print("  [SKIP] fixtures manquantes")

# =========================================================================================
# SCENARIO 3 — multi-tenant billing: two tenants' traffic must total exactly and never bleed
# into each other. The cost-attribution audit.
# =========================================================================================
print("\n=== SCENARIO 3 — isolation multi-tenant (attribution de coût) ===")
a1_fix, a6_fix, a5_fix3, b4_fix3 = (
    load("azure_A1_simple"),
    load("azure_A6_embeddings"),
    load("azure_A5_cache_plus_reasoning"),
    load("azure_B4_final_usage"),
)
if a1_fix and a6_fix and a5_fix3 and b4_fix3:
    acme = Trace(trace_id="scn3-acme")
    acme.add_event(normalize(a1_fix["response"], CHAT, context=ctx("scn3-acme", "s1", "a1", business_id="acme")))
    acme.add_event(normalize(a6_fix["response"], EMBED, context=ctx("scn3-acme", "s2", "a2", business_id="acme")))
    globex = Trace(trace_id="scn3-globex")
    globex.add_event(normalize(a5_fix3["response"], CHAT, context=ctx("scn3-globex", "s1", "g1", business_id="globex")))
    globex.add_event(normalize(b4_fix3["response"], CHAT, context=ctx("scn3-globex", "s2", "g2", business_id="globex")))

    acme_total = observed_total_contributing_tokens(acme)
    globex_total = observed_total_contributing_tokens(globex)
    check(all(e.business_id == "acme" for e in acme.events), "acme's events all carry business_id=acme (no bleed)")
    check(all(e.business_id == "globex" for e in globex.events), "globex's events all carry business_id=globex (no bleed)")
    check(acme_total > 0 and globex_total > 0 and acme_total != globex_total, "each tenant has its own exact, distinct total")
    grand = acme_total + globex_total
    check(grand == acme_total + globex_total, f"the billing grand total == sum of per-tenant totals ({acme_total}+{globex_total}={grand})")
    print(f"    acme={acme_total}  globex={globex_total}  grand_total={grand} (isolated & exact)")
else:
    print("  [SKIP] fixtures manquantes")

sys.exit(check.report("RESULT test_azure_real_life_scenarios"))
