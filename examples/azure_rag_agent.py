"""Azure confrontation runner — RAG AGENT pipeline (multi-span, real embed + generation).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\azure_rag_agent.py

Runs a REAL agentic RAG pipeline on Azure and confronts the tracker's multi-span guarantees:

    agent_run (root)
      ├─ embedding        REAL Azure embeddings call  -> a contributing TokenEvent
      ├─ vector_search    mock retriever, NATIVE metrics (num_results/latency), 0 tokens
      ├─ prompt_assembly  retrieved-context estimate, linked to the generation span
      └─ final_generation REAL Azure chat call (query + injected context) -> a TokenEvent

Confrontations (the RAG/agent tracker rules, on real data):
  - ATTRIBUTION : each real event attaches to its own span (embedding vs generation).
  - NO DOUBLE COUNT : the trace total = embedding + generation only. The retrieved-context /
    tool-result token counts are ESTIMATES for visibility, NEVER summed — the real cost is
    the generation call's input tokens (which already contain the injected context).
  - NATIVE SEARCH : vector_search is measured in num_results/latency, not tokens.

Same env + /openai/v1 Bearer surface as the other family runners.
"""

import datetime
import json
import os
import re
import sys
from urllib import error as urlerr
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "realistic")
sys.path.insert(0, ROOT)


def _load_dotenv() -> None:
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
RAW_ENDPOINT = os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
CHAT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
EMBEDDINGS = os.environ.get("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")

if not (API_KEY and RAW_ENDPOINT and CHAT):
    print("[SKIP] AZURE_OPENAI_API_KEY / endpoint / AZURE_OPENAI_DEPLOYMENT required. No call made (zero cost).")
    sys.exit(0)

_BASE = RAW_ENDPOINT.rstrip("/")
V1_BASE = _BASE if _BASE.endswith("/openai/v1") else f"{_BASE.split('/openai')[0]}/openai/v1"

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.analytics.rag import build_rag_summary  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span, record_tool_result  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context, record_vector_search  # noqa: E402

_results: list[tuple[str, str, str]] = []


def record(case: str, verdict: str, detail: str) -> None:
    _results.append((case, verdict, detail))
    print(f"  [{verdict}] {case}: {detail}")


def post(model: str, path: str, payload: dict) -> dict:
    url = f"{V1_BASE}/{path}"
    body = json.dumps({**payload, "model": model}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    req = urlreq.Request(url, data=body, method="POST", headers=headers)
    with urlreq.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def qty(ev, tt):
    q = next((x for x in ev.quantities if x.token_type == tt), None)
    return q.quantity if q else None


# --- a tiny local corpus + keyword retriever (mock retrieval; no fabricated tokens) --------
CORPUS = [
    "The Eiffel Tower is 330 meters tall and stands on the Champ de Mars in Paris, France.",
    "The Great Wall of China stretches for more than 21,000 kilometers across northern China.",
    "Mount Everest is the highest mountain above sea level, reaching 8,849 meters.",
    "The Amazon rainforest spans about 5.5 million square kilometers in South America.",
    "The Pacific Ocean is the largest and deepest of Earth's five oceans.",
]
QUERY = "How tall is the Eiffel Tower and where is it located?"
_STOP = {"the", "is", "and", "where", "it", "how", "of", "a", "in", "on", "for"}


def retrieve(query: str, k: int = 2) -> list[str]:
    words = {w for w in re.findall(r"[a-z]+", query.lower()) if w not in _STOP}
    scored = [(sum(w in doc.lower() for w in words), doc) for doc in CORPUS]
    return [doc for score, doc in sorted(scored, key=lambda p: -p[0]) if score > 0][:k]


trace_id = f"rag-{datetime.datetime.now():%H%M%S}"
ROOT_SPAN = "agent-root"
S_EMBED, S_SEARCH, S_ASSEMBLE, S_GEN = "span-embed", "span-search", "span-assemble", "span-gen"

print("\n=== Pipeline RAG agentique sur Azure (réel) ===")
try:
    # Root: an agent run
    agent = new_agent_span(trace_id, agent_run_id="rag-run-1", step_index=0, step_type="rag_query", span_id=ROOT_SPAN)

    # 1) EMBEDDING — real Azure embeddings call for the query
    embed_event = None
    if EMBEDDINGS:
        embed_resp = post(EMBEDDINGS, "embeddings", {"input": QUERY})
        embed_ctx = TraceContext(trace_id=trace_id, span_id=S_EMBED, request_correlation_id="r-embed", parent_span_id=ROOT_SPAN)
        embed_event = normalize(embed_resp, AzureOpenAIEmbeddingsAdapter(deployment=EMBEDDINGS), context=embed_ctx)
    embed_span = new_rag_span(trace_id, "embedding", parent_span_id=ROOT_SPAN, span_id=S_EMBED)

    # 2) VECTOR SEARCH — mock retrieval, recorded in NATIVE metrics. It is also the agent's
    # tool call, so it carries the tool metadata + a tool-result estimate (never summed).
    retrieved = retrieve(QUERY)
    context_text = "\n".join(retrieved)
    search_span = new_rag_span(
        trace_id,
        "vector_search",
        parent_span_id=ROOT_SPAN,
        span_id=S_SEARCH,
        metadata={"tool_name": "vector_search", "tool_call_id": "call-1"},
    )
    record_vector_search(search_span, num_results=len(retrieved), latency_ms=7.5, index="local-demo")
    record_tool_result(search_span, result_text=context_text, injected_into_context=True, next_llm_span_id=S_GEN)

    # 3) PROMPT ASSEMBLY — retrieved-context estimate, linked to the generation span
    assemble_span = new_rag_span(trace_id, "prompt_assembly", parent_span_id=ROOT_SPAN, span_id=S_ASSEMBLE)
    record_retrieved_context(assemble_span, context_text=context_text, injected_into_prompt=True, downstream_llm_span_id=S_GEN)

    # 4) FINAL GENERATION — real Azure chat call with the injected context
    augmented = f"Use the context to answer.\n\nContext:\n{context_text}\n\nQuestion: {QUERY}"
    gen_resp = post(CHAT, "chat/completions", {"messages": [{"role": "user", "content": augmented}], "max_completion_tokens": 256})
    gen_ctx = TraceContext(trace_id=trace_id, span_id=S_GEN, request_correlation_id="r-gen", parent_span_id=ROOT_SPAN)
    gen_event = normalize(gen_resp, AzureOpenAIChatCompletionsAdapter(deployment=CHAT), context=gen_ctx)
    gen_span = new_rag_span(trace_id, "final_generation", parent_span_id=ROOT_SPAN, span_id=S_GEN)

    # Save the real payloads
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, "azure_rag_agent.REAL.json"), "w", encoding="utf-8") as f:
        json.dump({"_SIMULATED": False, "query": QUERY, "retrieved": retrieved, "generation": gen_resp}, f, indent=2)

    # --- Assemble the trace ---
    trace = Trace(trace_id=trace_id)
    for s in (agent, embed_span, search_span, assemble_span, gen_span):
        trace.add_span(s)
    real_events = [e for e in (embed_event, gen_event) if e is not None]
    for e in real_events:
        trace.add_event(e)

    # --- Confrontations ---
    ctx_est = assemble_span.metadata["retrieved_context_estimated_tokens"]
    tool_est = search_span.metadata["tool_result_estimated_tokens"]
    total = observed_total_contributing_tokens(trace)
    real_sum = sum(e.event_contributing_tokens for e in real_events)

    record("attribution", "PASS" if gen_event.span_id == S_GEN else "DISCOVERY", f"generation event -> span {gen_event.span_id}")
    if embed_event is not None:
        record(
            "attribution_embed", "PASS" if embed_event.span_id == S_EMBED else "DISCOVERY", f"embedding event -> span {embed_event.span_id}"
        )

    reconc = all(e.event_total_mismatch in (0, None) and e.event_contributing_tokens == e.provider_total_tokens for e in real_events)
    record("reconcile", "PASS" if reconc else "DISCOVERY", f"each real event reconciles (n={len(real_events)})")

    naive = real_sum + ctx_est + tool_est
    no_double = total == real_sum and ctx_est > 0 and total < naive
    record(
        "no_double_count",
        "PASS" if no_double else "DISCOVERY",
        f"trace_total={total} (=embed+gen={real_sum}) | context_est={ctx_est} tool_est={tool_est} NOT summed " f"(naive_would_be={naive})",
    )

    gen_input = qty(gen_event, TokenType.INPUT) or 0
    landed = gen_input > ctx_est  # the real cost of the context is inside the generation input
    record(
        "cost_lands_in_llm",
        "PASS" if landed else "DISCOVERY",
        f"generation input={gen_input} > context_estimate={ctx_est} (paid once, in the LLM)",
    )

    native = search_span.metadata.get("measured_in_tokens") is False
    record("native_search", "PASS" if native else "DISCOVERY", "vector_search measured in num_results/latency, not tokens")

    summary = build_rag_summary(trace)
    # 4 RAG spans: embedding, vector_search, prompt_assembly, final_generation.
    rag_ok = summary.get("rag_span_count", 0) >= 4 and summary.get("injected_context_tokens", 0) > 0
    record(
        "rag_summary",
        "PASS" if rag_ok else "DISCOVERY",
        f"rag_span_count={summary.get('rag_span_count')} injected_context_tokens={summary.get('injected_context_tokens')}",
    )

except urlerr.HTTPError as exc:
    record("pipeline", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

print("\n" + "=" * 60)
print("RESUME RAG AGENT")
print("=" * 60)
counts: dict[str, int] = {}
for case, verdict, detail in _results:
    counts[verdict] = counts.get(verdict, 0) + 1
    print(f"  {case:20} {verdict:10} {detail}")
print("-" * 60)
print("  " + "  ".join(f"{v}={n}" for v, n in sorted(counts.items())))
sys.exit(0)
