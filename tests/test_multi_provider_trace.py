"""Extra — one trace mixing all providers, end-to-end (integration).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_multi_provider_trace.py

Normalizes a real-shaped (SIMULATED) call from each provider through the keystone, drops them
in one trace, and checks the contributing totals add up and survive export — and that the
provider-specific flags (Bedrock unverified cache) ride along.
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)["response"]


calls = [
    (OpenAIChatCompletionsAdapter(), "openai_chat_completions_cached_reasoning.SIMULATED.json", 1300, "openai"),
    (
        AzureOpenAIChatCompletionsAdapter(deployment="prod-gpt4o"),
        "realistic/azure_chat_content_filter.SIMULATED.json",
        1550,
        "azure_openai",
    ),
    (BedrockConverseAdapter(), "bedrock_converse_cache.SIMULATED.json", 1300, "bedrock"),
    (BedrockInvokeModelAdapter(), "realistic/bedrock_invoke_model_full.SIMULATED.json", 1225, "bedrock"),
    (GeminiGenerateContentAdapter(), "gemini_generate_content_thinking.SIMULATED.json", 1550, "gemini"),
    (AnthropicMessagesAdapter(), "anthropic_messages_cache.SIMULATED.json", 2220, "anthropic"),
]

trace = Trace(trace_id="multi")
for adapter, fixture, expected, provider in calls:
    ev = normalize(load(fixture), adapter, context=new_trace(trace_id=trace.trace_id))
    check(ev.provider == provider, f"{provider}: event provider set")
    check(ev.event_contributing_tokens == expected, f"{provider}: contributing == {expected}")
    trace.add_event(ev)

total = observed_total_contributing_tokens(trace)
expected_total = 1300 + 1550 + 1300 + 1225 + 1550 + 2220
check(total == expected_total, f"trace total == {expected_total} (got {total})")

# provider-specific flags ride along
bedrock_events = [e for e in trace.events if e.provider == "bedrock"]
check(any("unverified_additivity" in e.data_quality_flags for e in bedrock_events), "bedrock Converse event carries unverified_additivity")
check(any(e.api_surface == "invoke_model" and e.data_quality_flags == [] for e in bedrock_events), "bedrock InvokeModel event is clean")
check(
    all("unverified_additivity" not in e.data_quality_flags for e in trace.events if e.provider == "anthropic"),
    "anthropic cache additivity is verified",
)
check(
    all(e.data_quality_flags == [] for e in trace.events if e.provider in {"openai", "azure_openai", "gemini", "anthropic"}),
    "openai/azure/gemini/anthropic events are clean",
)
azure_event = next(e for e in trace.events if e.provider == "azure_openai")
check(
    azure_event.model == "gpt-4o-2024-08-06" and all(q.metadata.get("azure_deployment") == "prod-gpt4o" for q in azure_event.quantities),
    "azure event separates deployment metadata from response model",
)

# survives export
out_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demo_output",
    "multi_provider_trace_test",
)
os.makedirs(out_dir, exist_ok=True)
paths = export_csv(trace, out_dir)
with open(paths["token_events"], newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
csv_total = sum(int(r["event_contributing_tokens"]) for r in rows)
check(csv_total == total, "exported CSV total matches the model")

cov = build_coverage_exactness(trace)
check(cov["event_count"] == 6 and cov["observed_total_contributing_tokens"] == total, "coverage: 6 events, total agrees")
check({"bedrock", "azure_openai"} <= {e.provider for e in trace.events}, "AWS + Azure providers are both present")
check(len({e.provider for e in trace.events}) == 5, "five distinct providers in one trace")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
