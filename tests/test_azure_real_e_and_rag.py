"""GROUND TRUTH — the proxy==direct audit property and the RAG no-double-count, locked in CI.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_azure_real_e_and_rag.py

Replays the REAL Azure payloads captured by examples/azure_matrix_family_e.py and
examples/azure_rag_agent.py, entirely offline, so two more pillars become permanent
regressions (no key, no cost):

  - AUDIT (Family E): a recorded real response is served by a fake upstream and routed through
    the tracker's REAL loopback proxy; the proxy's inline capture must equal a direct
    normalization of the same response — two independent paths, identical accounting.
  - RAG no-double-count: the real generation response is placed on its span in a RAG trace;
    the trace total counts the LLM event only, while the retrieved-context and tool-result
    token counts stay ESTIMATES for visibility and are NEVER summed (that would double count).

Each section skips cleanly if its fixture is absent; the fixtures are committed, so CI runs it.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.analytics.rag import build_rag_summary  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.proxy.server import ProxyConfig, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402
from tracker.workflows.agent_tracker import new_agent_span, record_tool_result  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context, record_vector_search  # noqa: E402

check = make_checker()
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic")


def load(name: str):
    path = os.path.join(FIX, f"{name}.REAL.json")
    if not os.path.exists(path):
        print(f"[SKIP] {name}.REAL.json absent — run the matrix runner to capture it.")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def qmap(ev) -> dict:
    return {q.token_type.value: q.quantity for q in ev.quantities}


def _fake_upstream(response: dict) -> ThreadingHTTPServer:
    body = json.dumps(response).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def replay_e(fixture: str, client_path: str, client_body: dict, adapter, label: str) -> None:
    data = load(fixture)
    if not data:
        return
    check(data.get("_SIMULATED") is False, f"{label}: REAL captured payload")
    response = data["response"]

    upstream = _fake_upstream(response)
    scratch = os.path.join(os.getcwd(), f".test_e_rag_{fixture}.jsonl")
    with open(scratch, "w", encoding="utf-8"):
        pass
    captured: list = []
    proxy = None
    try:
        repo = FileRepository(scratch)
        proxy = create_proxy_server(
            repo,
            ProxyConfig(provider="azure_openai", upstream_base_url=f"http://127.0.0.1:{upstream.server_address[1]}", port=0),
            on_event=captured.append,
        )
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{proxy.server_address[1]}{client_path}"
        req = urlreq.Request(
            url,
            data=json.dumps(client_body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer test"},
        )
        with urlreq.urlopen(req, timeout=10) as resp:
            resp.read()
    finally:
        if proxy is not None:
            proxy.shutdown()
        upstream.shutdown()
        if os.path.exists(scratch):
            os.remove(scratch)

    check(len(captured) == 1, f"{label}: the proxy captured exactly one event from the real response")
    if not captured:
        return
    event_proxy = captured[-1]
    event_direct = normalize(response, adapter, context=None)
    check(qmap(event_proxy) == qmap(event_direct), f"{label}: proxy quantities == direct quantities (identical accounting)")
    check(event_proxy.provider_total_tokens == event_direct.provider_total_tokens, f"{label}: same provider total on both paths")
    check(
        event_proxy.event_contributing_tokens == event_direct.event_contributing_tokens,
        f"{label} GROUND TRUTH: proxy and direct agree on the contributing total — no token lost or invented",
    )


# --- E1 / E3 — proxy vs direct on recorded real Azure responses -----------------------------
replay_e(
    "azure_E1_proxy_vs_direct_chat",
    "/openai/v1/chat/completions",
    {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "hi"}]},
    AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini"),
    "E1 chat",
)
replay_e(
    "azure_E3_proxy_vs_direct_embeddings",
    "/openai/v1/embeddings",
    {"model": "text-embedding-3-large", "input": "hi"},
    AzureOpenAIEmbeddingsAdapter(deployment="text-embedding-3-large"),
    "E3 embeddings",
)


# --- RAG no-double-count on the real generation response ------------------------------------
rag = load("azure_rag_agent")
if rag:
    check(rag.get("_SIMULATED") is False, "RAG: REAL captured pipeline")
    trace_id = "rag-ci"
    root, s_embed, s_search, s_assemble, s_gen = "agent-root", "span-embed", "span-search", "span-assemble", "span-gen"
    context_text = "\n".join(rag["retrieved"])

    agent = new_agent_span(trace_id, agent_run_id="ci", step_index=0, step_type="rag_query", span_id=root)
    embed_span = new_rag_span(trace_id, "embedding", parent_span_id=root, span_id=s_embed)
    search_span = new_rag_span(trace_id, "vector_search", parent_span_id=root, span_id=s_search)
    record_vector_search(search_span, num_results=len(rag["retrieved"]), latency_ms=7.5)
    record_tool_result(search_span, result_text=context_text, injected_into_context=True, next_llm_span_id=s_gen)
    assemble_span = new_rag_span(trace_id, "prompt_assembly", parent_span_id=root, span_id=s_assemble)
    record_retrieved_context(assemble_span, context_text=context_text, injected_into_prompt=True, downstream_llm_span_id=s_gen)
    gen_span = new_rag_span(trace_id, "final_generation", parent_span_id=root, span_id=s_gen)

    gen_ctx = TraceContext(trace_id=trace_id, span_id=s_gen, request_correlation_id="r-gen", parent_span_id=root)
    gen_event = normalize(rag["generation"], AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini"), context=gen_ctx)

    trace = Trace(trace_id=trace_id)
    for s in (agent, embed_span, search_span, assemble_span, gen_span):
        trace.add_span(s)
    trace.add_event(gen_event)

    check(gen_event.span_id == s_gen, "RAG: the generation event attaches to the final_generation span")
    reconc = gen_event.event_total_mismatch == 0 and gen_event.event_contributing_tokens == gen_event.provider_total_tokens
    check(reconc, "RAG: the real generation reconciles (contributing == provider total)")

    total = observed_total_contributing_tokens(trace)
    ctx_est = assemble_span.metadata["retrieved_context_estimated_tokens"]
    tool_est = search_span.metadata["tool_result_estimated_tokens"]
    naive = total + ctx_est + tool_est
    check(ctx_est > 0, "RAG: the retrieved-context estimate is recorded (for visibility)")
    check(
        total == gen_event.event_contributing_tokens and total < naive,
        f"RAG GROUND TRUTH: trace total={total} counts the LLM only; context/tool estimates ({ctx_est}/{tool_est}) NOT summed "
        f"(a naive tracker would report {naive} — double counting the context)",
    )
    check(search_span.metadata.get("measured_in_tokens") is False, "RAG: vector_search is native (num_results/latency), not tokens")
    summary = build_rag_summary(trace)
    check(summary.get("rag_span_count", 0) >= 4, "RAG: build_rag_summary sees the four RAG spans")


# --- RAG control — retrieval, not memory, drives the answer (behavioral proof) --------------
# A fabricated fact the model cannot know: the number appears ONLY when the context is injected.
control = load("azure_rag_control")
if control:
    check(control.get("_SIMULATED") is False, "RAG control: REAL captured two-arm run")
    token = control["fact_token"]
    with_answer = (control["with_context"]["choices"][0].get("message") or {}).get("content") or ""
    without_answer = (control["without_context"]["choices"][0].get("message") or {}).get("content") or ""
    check(token in with_answer, f"RAG control: WITH context, the answer contains the fabricated {token!r} (retrieval was used)")
    check(
        token not in without_answer,
        f"RAG control GROUND TRUTH: WITHOUT context the answer never contains {token!r} — "
        "the fact came from retrieval, not the model's memory",
    )

sys.exit(check.report("RESULT test_azure_real_e_and_rag"))
