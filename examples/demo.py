"""End-to-end DEMO — a realistic RAG + agent run, exported to CSV + Excel.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\demo.py

No API credit needed: it drives SIMULATED provider payloads through the real tracker
(context propagation -> adapters -> normalizer -> trace -> rollup -> export). It produces
files you can open under  demo_output/  :
    token_events.csv, token_quantities.csv, token_spans.csv, tokens.xlsx

The run it simulates (one trace):
  1. RAG retrieval        — a vector_search span (native metrics) + prompt_assembly (context)
  2. generation #1        — an OpenAI Chat Completions call (cached + reasoning)
  3. an agent tool call   — search_orders, whose result feeds the next prompt
  4. generation #2        — an Anthropic Messages call (cache fields stay unverified)
  5. a streamed answer    — interrupted (partial estimate) then resolved (supersedes it)
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures")
OUT_DIR = os.path.join(PROJECT_ROOT, "demo_output")
sys.path.insert(0, PROJECT_ROOT)

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.context.propagation import span, trace  # noqa: E402
from tracker.derive.trace_rollup import roll_up  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.export.excel_exporter import export_excel  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402
from tracker.workflows.agent_tracker import new_tool_span, record_tool_result  # noqa: E402
from tracker.workflows.rag_tracker import new_rag_span, record_retrieved_context, record_vector_search  # noqa: E402


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)["response"]


def run():
    with trace(business_id="acme-support-bot", workflow="rag_agent", environment="prod") as root:
        tr = Trace(trace_id=root.trace_id, business_id=root.business_id, workflow=root.workflow, environment=root.environment)

        # 1) RAG retrieval -------------------------------------------------------------
        with span() as vs_ctx:
            vs = new_rag_span(root.trace_id, "vector_search", parent_span_id=root.span_id, span_id=vs_ctx.span_id)
            record_vector_search(vs, num_results=5, latency_ms=8.3, index="faiss")
            tr.add_span(vs)
        with span() as pa_ctx:
            pa = new_rag_span(root.trace_id, "prompt_assembly", parent_span_id=root.span_id, span_id=pa_ctx.span_id)
            record_retrieved_context(pa, context_text="Order policy: refunds within 30 days. " * 12, injected_into_prompt=True)
            tr.add_span(pa)

        # 2) generation #1 — OpenAI ----------------------------------------------------
        with span():
            tr.add_event(normalize(load("openai_chat_completions_cached_reasoning.SIMULATED.json"), OpenAIChatCompletionsAdapter()))

        # 3) agent tool call -----------------------------------------------------------
        with span() as tool_ctx:
            tool = new_tool_span(
                root.trace_id, tool_name="search_orders", tool_call_id="call-1", parent_span_id=root.span_id, span_id=tool_ctx.span_id
            )
            record_tool_result(tool, result_text="3 orders found for customer #8842. " * 8, injected_into_context=True)
            tr.add_span(tool)

        # 4) generation #2 — Anthropic -------------------------------------------------
        with span():
            tr.add_event(normalize(load("anthropic_messages_cache.SIMULATED.json"), AnthropicMessagesAdapter()))

        # 5) streamed answer — interrupted, then resolved (supersession) ---------------
        with span() as s_ctx:
            st = StreamTracker.from_context(s_ctx, provider="openai", api_surface="chat_completions", model="gpt-4o")
            st.feed("Here is what I found for your ")
            st.feed("recent orders and the refund ")
            partial = st.interrupt()  # the stream dropped
            final = st.resolve_with_final_usage(output_tokens=120, input_tokens=300, provider_total_tokens=420)
            tr.add_event(partial)
            tr.add_event(final)

    return tr


def print_summary(tr):
    rollup = roll_up(tr)
    cov = build_coverage_exactness(tr)
    print("=" * 68)
    print(f"  TRACE {tr.trace_id[:12]}...   workflow={tr.workflow}   env={tr.environment}")
    print("=" * 68)
    print(f"  {'event':<10}{'provider':<12}{'surface':<18}{'contributing':>12}  flags")
    print("  " + "-" * 64)
    for e in tr.events:
        flags = ",".join(e.data_quality_flags) or "-"
        sup = "  (superseded)" if e.superseded else ""
        print(
            f"  {e.event_id[:9]:<10}{(e.provider or '-'):<12}{(e.api_surface or '-'):<18}"
            f"{e.event_contributing_tokens:>12}  {flags}{sup}"
        )
    print("  " + "-" * 64)
    print(f"  TOTAL contributing tokens : {rollup.observed_total_contributing_tokens}")
    print(
        f"  events={rollup.event_count}  superseded={rollup.superseded_event_count}  "
        f"flagged={rollup.flagged_event_count}  spans={len(tr.spans)}"
    )
    print(
        f"  exact quantities={cov['exact_quantity_count']}  estimate={cov['estimate_quantity_count']}  "
        f"unknown={cov['unknown_quantity_count']}  exactness={cov['exactness_ratio']}"
    )
    print("=" * 68)


def main():
    tr = run()
    print_summary(tr)
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = export_csv(tr, OUT_DIR)
    xlsx = export_excel(tr, os.path.join(OUT_DIR, "tokens.xlsx"))
    print("\nFichiers exportes (a ouvrir) :")
    for label, p in {**paths, "excel": xlsx}.items():
        print(f"  - {label:<18} {p}")


if __name__ == "__main__":
    main()
