"""DEEP RAG pipeline x multi-model matrix — the FULL RAG chain (embedding -> vector_search ->
reranking -> prompt_assembly -> final_generation) run across many REAL model names per
provider/stage, not one generic placeholder. Catches model-specific quirks (reasoning-heavy
models, mini vs full-size contexts, embedding-only calls) and confirms the no-double-count
rule (retrieved-context estimate + vector-search metrics + rerank cost + generation cost)
holds regardless of which model is used at each stage.

Run: python tests/test_rag_multimodel_deep.py

Three parts:
  1. Full RAG pipeline, once per (embedding model, generation model) combination drawn from
     a real-name matrix across OpenAI, Azure, Anthropic, Gemini, Bedrock, Mistral, Cohere —
     verifying: embedding cost counts, vector_search contributes 0, rerank cost counts,
     retrieved-context ESTIMATE never double-counted, model name threads through correctly.
  2. Reasoning/thinking-heavy model variants (o-series, Gemini thinking) at randomized EXTREME
     ratios (reasoning >> output) — still reconciles exactly.
  3. Randomized end-to-end RAG runs across the full model matrix, many iterations, indepen-
     dently-computed expected totals (embedding + rerank + generation only).
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.cohere_chat_adapter import CohereChatAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.mistral_chat_adapter import MistralChatAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_embeddings_adapter import OpenAIEmbeddingsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter  # noqa: E402
from tracker.context.propagation import new_trace, span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context, record_vector_search  # noqa: E402

_failures = 0
_checks = 0
SEED = int(os.environ.get("FUZZ_SEED", "424242"))
rng = random.Random(SEED)


def check(cond, msg):
    global _failures, _checks
    _checks += 1
    if not cond:
        _failures += 1
        print(f"[FAIL] (seed={SEED}) {msg}")


_uid = 0


def uid(prefix="r"):
    global _uid
    _uid += 1
    return f"{prefix}-{_uid}"


# =====================================================================================
# Model matrices — REAL model names per provider, spanning mini/full/reasoning/embedding
# =====================================================================================

EMBEDDING_MODELS = [
    ("openai_small", OpenAIEmbeddingsAdapter, "text-embedding-3-small"),
    ("openai_large", OpenAIEmbeddingsAdapter, "text-embedding-3-large"),
    ("openai_ada", OpenAIEmbeddingsAdapter, "text-embedding-ada-002"),
]

# (label, adapter_factory, model_name, is_reasoning_heavy, provider_kind)
GENERATION_MODELS = [
    ("gpt-4o", OpenAIChatCompletionsAdapter, "gpt-4o-2024-08-06", False, "openai_chat"),
    ("gpt-4o-mini", OpenAIChatCompletionsAdapter, "gpt-4o-mini-2024-07-18", False, "openai_chat"),
    ("o1", OpenAIResponsesAdapter, "o1-2024-12-17", True, "openai_responses"),
    ("o3-mini", OpenAIResponsesAdapter, "o3-mini-2025-01-31", True, "openai_responses"),
    ("o4-mini", OpenAIResponsesAdapter, "o4-mini-2025-04-16", True, "openai_responses"),
    ("azure-gpt-4o", AzureOpenAIChatCompletionsAdapter, "gpt-4o-2024-08-06", False, "openai_chat"),
    ("claude-opus-4-8", AnthropicMessagesAdapter, "claude-opus-4-8", False, "anthropic"),
    ("claude-sonnet-5", AnthropicMessagesAdapter, "claude-sonnet-5", False, "anthropic"),
    ("claude-haiku-4-5", AnthropicMessagesAdapter, "claude-haiku-4-5-20251001", False, "anthropic"),
    ("gemini-2.5-pro", GeminiGenerateContentAdapter, "gemini-2.5-pro", True, "gemini"),
    ("gemini-2.5-flash", GeminiGenerateContentAdapter, "gemini-2.5-flash", True, "gemini"),
    ("gemini-2.5-flash-lite", GeminiGenerateContentAdapter, "gemini-2.5-flash-lite", False, "gemini"),
    ("nova-pro", BedrockConverseAdapter, "amazon.nova-pro-v1:0", False, "bedrock_converse"),
    ("nova-micro", BedrockConverseAdapter, "amazon.nova-micro-v1:0", False, "bedrock_converse"),
    ("mistral-large", MistralChatAdapter, "mistral-large-latest", False, "openai_chat"),
    ("mistral-small", MistralChatAdapter, "mistral-small-latest", False, "openai_chat"),
    ("command-r-plus", CohereChatAdapter, "command-r-plus-08-2024", False, "cohere"),
]

RERANK_MODELS = ["rerank-2", "rerank-2-lite", "rerank-1"]


def gen_usage_and_expected(kind, reasoning_heavy, prompt_tokens, output_tokens):
    """Build a (payload_fields_only, expected_contribution) pair for one provider 'kind'."""
    if kind == "openai_chat":
        reasoning = (
            int(output_tokens * rng.uniform(0.5, 0.9))
            if reasoning_heavy
            else (int(output_tokens * rng.uniform(0, 0.1)) if rng.random() < 0.3 else 0)
        )
        cached = int(prompt_tokens * rng.uniform(0, 0.6)) if rng.random() < 0.5 else 0
        total = prompt_tokens + output_tokens
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        }
        return {"usage": usage}, total
    if kind == "openai_responses":
        reasoning = int(output_tokens * rng.uniform(0.5, 0.9)) if reasoning_heavy else 0
        cached = int(prompt_tokens * rng.uniform(0, 0.6)) if rng.random() < 0.5 else 0
        total = prompt_tokens + output_tokens
        usage = {
            "input_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning},
        }
        return {"usage": usage}, total
    if kind == "anthropic":
        cache_read = int(prompt_tokens * rng.uniform(0, 0.5)) if rng.random() < 0.5 else 0
        cache_creation = int(prompt_tokens * rng.uniform(0, 0.2)) if rng.random() < 0.3 else 0
        expected = prompt_tokens + output_tokens + cache_read + cache_creation
        usage = {
            "input_tokens": prompt_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        }
        return {"usage": usage}, expected
    if kind == "gemini":
        thoughts = int(output_tokens * rng.uniform(1.0, 4.0)) if reasoning_heavy else 0  # thinking can EXCEED output
        cached = int(prompt_tokens * rng.uniform(0, 0.4)) if rng.random() < 0.4 else 0
        total = prompt_tokens + output_tokens + thoughts
        usage = {
            "usageMetadata": {
                "promptTokenCount": prompt_tokens,
                "candidatesTokenCount": output_tokens,
                "totalTokenCount": total,
                "cachedContentTokenCount": cached,
                "thoughtsTokenCount": thoughts,
            }
        }
        return usage, total
    if kind == "bedrock_converse":
        total = prompt_tokens + output_tokens
        usage = {"usage": {"inputTokens": prompt_tokens, "outputTokens": output_tokens, "totalTokens": total}}
        return usage, total
    if kind == "cohere":
        return {"usage": {"tokens": {"input_tokens": prompt_tokens, "output_tokens": output_tokens}}}, prompt_tokens + output_tokens
    raise ValueError(kind)


# =====================================================================================
# PART 1 — full RAG pipeline once per (embedding model, generation model) combination
# =====================================================================================
print("--- Part 1: full RAG pipeline across the embedding x generation model matrix ---")

POLICY_TEXT = (
    "Refunds are issued within 30 days of delivery for unused items in original packaging. "
    "International orders bear return shipping unless the item arrived damaged. Final-sale "
    "clearance items are excluded except for confirmed manufacturing defects."
) * 3  # a substantial, realistic-length retrieved chunk

n_combo = 0
for emb_label, emb_cls, emb_model in EMBEDDING_MODELS:
    for gen_label, gen_cls, gen_model, reasoning_heavy, kind in GENERATION_MODELS:
        n_combo += 1
        embed_prompt = rng.randint(20, 400)
        with trace(business_id="rag-matrix", workflow="rag_pipeline") as root:
            tr = Trace(trace_id=root.trace_id)

            # 1) embedding step: a REAL, contributing cost
            with span() as emb_ctx:
                emb_payload = {"model": emb_model, "usage": {"prompt_tokens": embed_prompt, "total_tokens": embed_prompt}}
                emb_ev = normalize(emb_payload, emb_cls(), context=emb_ctx)
            tr.add_event(emb_ev)
            check(emb_ev.model == emb_model, f"{emb_label}: embedding model name threads through correctly")
            check(emb_ev.event_contributing_tokens == embed_prompt, f"{emb_label}: embedding contributes its real cost ({embed_prompt})")

            # 2) vector_search: NATIVE metrics, contributes 0 tokens (it's a span, not an event)
            with span() as vs_ctx:
                vs = new_rag_span(tr.trace_id, "vector_search", parent_span_id=root.span_id, span_id=vs_ctx.span_id)
                record_vector_search(vs, num_results=rng.randint(1, 10), latency_ms=rng.uniform(1, 200), index="policies")
                tr.add_span(vs)
            check(vs.metadata["measured_in_tokens"] is False, f"{emb_label}: vector_search explicitly not token-measured")

            # 3) reranking: a REAL contributing cost (token-billing reranker)
            rerank_model = rng.choice(RERANK_MODELS)
            rerank_total = rng.randint(50, 3000)
            with span() as rr_ctx:
                rr_ev = normalize({"model": rerank_model, "usage": {"total_tokens": rerank_total}}, VoyageRerankAdapter(), context=rr_ctx)
            tr.add_event(rr_ev)
            check(rr_ev.event_contributing_tokens == rerank_total, f"rerank ({rerank_model}): contributes its real cost ({rerank_total})")

            # 4) prompt_assembly: retrieved-context ESTIMATE, annotation only, never counted
            with span() as pa_ctx:
                pa = new_rag_span(tr.trace_id, "prompt_assembly", parent_span_id=root.span_id, span_id=pa_ctx.span_id)
                record_retrieved_context(pa, context_text=POLICY_TEXT, injected_into_prompt=True)
                tr.add_span(pa)
            ctx_estimate = pa.metadata["retrieved_context_estimated_tokens"]
            check(ctx_estimate > 0, f"{emb_label}/{gen_label}: retrieved-context estimate is nonzero")

            # 5) final_generation: a REAL contributing cost, with model-specific quirks
            gen_prompt = rng.randint(300, 6000)
            gen_output = rng.randint(20, 1500)
            fields, expected_gen = gen_usage_and_expected(kind, reasoning_heavy, gen_prompt, gen_output)
            payload = {"model": gen_model, **fields} if "usageMetadata" not in fields else {"modelVersion": gen_model, **fields}
            with span() as gen_ctx:
                gen_ev = normalize(payload, gen_cls(), context=gen_ctx)
            tr.add_event(gen_ev)
            if kind == "bedrock_converse":
                # Confirmed against a REAL captured payload: Bedrock Converse never echoes
                # the model back in the response body, so None is the correct value here,
                # not a bug.
                check(
                    gen_ev.model is None, f"{gen_label}: Bedrock Converse correctly reports no model in the body (real-payload-confirmed)"
                )
            else:
                check(
                    gen_ev.model == gen_model,
                    f"{gen_label}: generation model name threads through correctly ({gen_ev.model!r} != {gen_model!r})",
                )
            check(gen_ev.event_total_mismatch in (0, None), f"{gen_label}: reconciles against its own provider total")
            check(
                gen_ev.event_contributing_tokens == expected_gen,
                f"{gen_label}: contributes the independently-expected total ({gen_ev.event_contributing_tokens} != {expected_gen})",
            )

        # THE core RAG invariant: pipeline total == embedding + rerank + generation ONLY —
        # the retrieved-context estimate and vector_search metrics are NEVER added.
        pipeline_total = observed_total_contributing_tokens(tr)
        expected_pipeline = embed_prompt + rerank_total + expected_gen
        check(
            pipeline_total == expected_pipeline,
            f"{emb_label}/{gen_label}: pipeline total == embedding+rerank+generation ({pipeline_total} != {expected_pipeline})",
        )
        check(
            pipeline_total != expected_pipeline + ctx_estimate,
            f"{emb_label}/{gen_label}: retrieved-context estimate ({ctx_estimate}) is NOT double-counted into the pipeline total",
        )

print(f"[INFO] Part 1: {n_combo} full RAG pipelines across {len(EMBEDDING_MODELS)} embedding x {len(GENERATION_MODELS)} generation models.")

# =====================================================================================
# PART 2 — extreme reasoning/thinking ratios (randomized), still reconciles exactly
# =====================================================================================
print("\n--- Part 2: extreme reasoning/thinking ratios across randomized magnitudes ---")

REASONING_MODELS = [m for m in GENERATION_MODELS if m[3]]
N_EXTREME = 30
for _ in range(N_EXTREME):
    gen_label, gen_cls, gen_model, _, kind = rng.choice(REASONING_MODELS)
    prompt_tokens = rng.randint(50, 20000)
    output_tokens = rng.randint(1, 50)  # tiny visible output
    fields, expected = gen_usage_and_expected(kind, True, prompt_tokens, output_tokens)
    payload = {"model": gen_model, **fields} if "usageMetadata" not in fields else {"modelVersion": gen_model, **fields}
    ev = normalize(payload, gen_cls(), context=new_trace())
    check(ev.event_total_mismatch in (0, None), f"extreme reasoning ({gen_label}): reconciles even when reasoning >> visible output")
    check(
        ev.event_contributing_tokens == expected,
        f"extreme reasoning ({gen_label}): contributes exactly the expected total ({ev.event_contributing_tokens} != {expected})",
    )
    check(
        ev.event_contributing_tokens >= prompt_tokens + output_tokens,
        f"extreme reasoning ({gen_label}): total is never LESS than the visible input+output "
        "(reasoning is additive/subtotal, never negative)",
    )

print(f"[INFO] Part 2: {N_EXTREME} extreme-ratio reasoning/thinking calls, all reconciled.")

# =====================================================================================
# PART 3 — randomized end-to-end RAG runs across the full model matrix
# =====================================================================================
print("\n--- Part 3: randomized end-to-end RAG runs, many iterations ---")

N_RANDOM_RUNS = 80
for i in range(N_RANDOM_RUNS):
    emb_label, emb_cls, emb_model = rng.choice(EMBEDDING_MODELS)
    gen_label, gen_cls, gen_model, reasoning_heavy, kind = rng.choice(GENERATION_MODELS)
    embed_prompt = rng.randint(1, 500)
    do_rerank = rng.random() < 0.6
    rerank_total = rng.randint(1, 2000) if do_rerank else 0
    gen_prompt = rng.randint(1, 8000)
    gen_output = rng.randint(0, 2000)

    run_trace_id = uid("t-rag-random")
    tr = Trace(trace_id=run_trace_id)

    def _ctx():
        return new_trace(trace_id=run_trace_id)  # noqa: B023 - always called within THIS iteration, never stored for later

    tr.add_event(
        normalize({"model": emb_model, "usage": {"prompt_tokens": embed_prompt, "total_tokens": embed_prompt}}, emb_cls(), context=_ctx())
    )
    if do_rerank:
        tr.add_event(normalize({"model": "rerank-2", "usage": {"total_tokens": rerank_total}}, VoyageRerankAdapter(), context=_ctx()))
    fields, expected_gen = gen_usage_and_expected(kind, reasoning_heavy, gen_prompt, gen_output)
    payload = {"model": gen_model, **fields} if "usageMetadata" not in fields else {"modelVersion": gen_model, **fields}
    tr.add_event(normalize(payload, gen_cls(), context=_ctx()))

    # a randomly-sized retrieved-context annotation (span only, no event) — must never affect the total
    pa_ctx = _ctx()
    pa = new_rag_span(tr.trace_id, "prompt_assembly", span_id=pa_ctx.span_id)
    record_retrieved_context(pa, context_text="chunk " * rng.randint(1, 500), injected_into_prompt=rng.random() < 0.9)
    tr.add_span(pa)

    expected_total = embed_prompt + rerank_total + expected_gen
    got_total = observed_total_contributing_tokens(tr)
    check(
        got_total == expected_total,
        f"random RAG run #{i} ({emb_label}+{gen_label}, rerank={do_rerank}): total matches ({got_total} != {expected_total})",
    )

print(f"[INFO] Part 3: {N_RANDOM_RUNS} randomized end-to-end RAG runs across the full model matrix.")

print(f"\n[INFO] total checks run: {_checks}   (seed={SEED}, reproducible)")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
