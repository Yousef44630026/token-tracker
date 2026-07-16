"""Extra — Azure OpenAI adapters (same wire format as OpenAI, provider label differs).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_azure_openai_adapters.py

Azure OpenAI IS the OpenAI API on Azure infra: the response `usage` shape is identical, so
the adapters subclass the OpenAI ones and only change provider -> "azure_openai". The
additivity table aliases azure_openai -> openai, so cached/reasoning stay subtotals and the
no-double-count guarantee holds. We reuse the OpenAI SIMULATED fixtures precisely to show the
format is shared.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
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


def by_type(usage, tt):
    return next((q for q in usage.quantities if q.token_type == tt), None)


def all_have_deployment(usage, deployment):
    return all(q.metadata.get("azure_deployment") == deployment for q in usage.quantities)


# reuse documents the shared format
check(issubclass(AzureOpenAIResponsesAdapter, OpenAIResponsesAdapter), "Azure Responses subclasses OpenAI Responses")
check(issubclass(AzureOpenAIChatCompletionsAdapter, OpenAIChatCompletionsAdapter), "Azure Chat subclasses OpenAI Chat")


def assert_surface(label, adapter, surface, fixture):
    usage = adapter.extract_usage_from_response(load(fixture))
    check(usage.provider == "azure_openai", f"{label}: provider == azure_openai")
    check(usage.api_surface == surface, f"{label}: api_surface == {surface}")
    cached = by_type(usage, TokenType.CACHED_INPUT)
    reasoning = by_type(usage, TokenType.REASONING)
    check(cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input", f"{label}: cached subtotal_of input (alias)")
    check(
        reasoning.additivity == Additivity.SUBTOTAL_OF and reasoning.subtotal_of == "output",
        f"{label}: reasoning subtotal_of output (alias)",
    )
    check(usage.provider_total_tokens == 1300, f"{label}: provider_total_tokens == 1300")
    event = TokenEvent(
        event_id=f"evt-{label}",
        request_correlation_id="r",
        trace_id="t",
        span_id="s",
        provider=usage.provider,
        api_surface=usage.api_surface,
        model=usage.model,
        quantities=usage.quantities,
        provider_total_tokens=usage.provider_total_tokens,
        observation={"authoritative": True},
    )
    check(event.event_contributing_tokens == 1300 and event.event_total_mismatch == 0, f"{label}: contributing == 1300, no mismatch")


assert_surface("azure-responses", AzureOpenAIResponsesAdapter(), "responses", "openai_responses_cached_reasoning.SIMULATED.json")
assert_surface(
    "azure-chat", AzureOpenAIChatCompletionsAdapter(), "chat_completions", "openai_chat_completions_cached_reasoning.SIMULATED.json"
)

# --- B4: Azure deployment name is tracked separately from response model ---
deployment = "prod-gpt4o-deployment"
chat_usage = AzureOpenAIChatCompletionsAdapter(deployment=deployment).extract_usage_from_response(
    load("openai_chat_completions_cached_reasoning.SIMULATED.json")
)
check(chat_usage.model == "o4-mini-2025-04-16", "B4 chat: response model is preserved")
check(all_have_deployment(chat_usage, deployment), "B4 chat: deployment stored in quantity metadata")

responses_usage = AzureOpenAIResponsesAdapter(deployment_name=deployment).extract_usage_from_response(
    load("openai_responses_cached_reasoning.SIMULATED.json")
)
check(responses_usage.model == "o4-mini-2025-04-16", "B4 responses: response model is preserved")
check(all_have_deployment(responses_usage, deployment), "B4 responses: deployment_name alias stored in metadata")

stream_usage = AzureOpenAIChatCompletionsAdapter(deployment=deployment).extract_usage_from_stream_event(
    {"model": "gpt-4o-2024-08-06", "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
)
check(stream_usage is not None and stream_usage.model == "gpt-4o-2024-08-06", "B4 stream: response model is preserved")
check(stream_usage is not None and all_have_deployment(stream_usage, deployment), "B4 stream: deployment metadata retained")

try:
    AzureOpenAIResponsesAdapter(deployment="a", deployment_name="b")
except ValueError:
    disagree_raises = True
else:
    disagree_raises = False
check(disagree_raises, "B4: conflicting deployment names are rejected")

# --- flows through the normalizer keystone with the azure provider label ---
ev = normalize(load("openai_chat_completions_cached_reasoning.SIMULATED.json"), AzureOpenAIChatCompletionsAdapter(), context=new_trace())
check(ev.provider == "azure_openai" and ev.event_contributing_tokens == 1300, "normalize() yields an azure_openai event (1300)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
