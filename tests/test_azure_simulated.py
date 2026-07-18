"""Azure OpenAI simulated coverage — content-filter noise, Responses, streaming.

Run: python tests/test_azure_simulated.py

Azure adds content-filter fields around the usage object; the adapter must ignore them and
still extract usage. Responses + streaming reuse the OpenAI wire format with the azure_openai
provider label (aliased to openai for additivity).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

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


# ===== B1. Azure Chat with content-filter noise around usage =====
ev = normalize(load("azure_chat_content_filter.SIMULATED.json"), AzureOpenAIChatCompletionsAdapter(), context=new_trace())
check(ev.provider == "azure_openai" and ev.api_surface == "chat_completions", "Azure Chat: provider label")
check(
    q(ev, TokenType.INPUT).quantity == 1200 and q(ev, TokenType.OUTPUT).quantity == 350,
    "Azure Chat: usage extracted despite content-filter fields",
)
check(q(ev, TokenType.INPUT).additivity == Additivity.TOTAL_CONTRIBUTING, "Azure Chat: input total_contributing (alias->openai)")
check(ev.event_contributing_tokens == 1550 and ev.event_total_mismatch == 0, "Azure Chat: 1550, reconciles")
check(ev.data_quality_flags == [], "Azure Chat: clean (filter fields ignored, no spurious flag)")

# ===== B2. Azure Responses (reuses the OpenAI Responses wire format) =====
ev = normalize(load("openai_responses_full.SIMULATED.json"), AzureOpenAIResponsesAdapter(), context=new_trace())
check(ev.provider == "azure_openai" and ev.api_surface == "responses", "Azure Responses: provider label")
check(q(ev, TokenType.REASONING).additivity == Additivity.SUBTOTAL_OF, "Azure Responses: reasoning subtotal (alias)")
check(ev.event_contributing_tokens == 2948 and ev.event_total_mismatch == 0, "Azure Responses: 2948, reconciles")


# ===== B3. Azure streaming (OpenAI-shaped final usage chunk) =====
def openai_text(event):
    choices = event.get("choices") or []
    return choices[0].get("delta", {}).get("content") if choices else None


azure_stream = [
    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": " there"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 80, "completion_tokens": 12, "total_tokens": 92}},
]
ev = consume_stream(azure_stream, AzureOpenAIChatCompletionsAdapter(), context=new_trace(), text_extractor=openai_text)
check(ev.provider == "azure_openai", "Azure stream: provider label")
check(
    q(ev, TokenType.OUTPUT).precision_level == PrecisionLevel.EXACT and q(ev, TokenType.OUTPUT).quantity == 12,
    "Azure stream: EXACT output 12",
)
check(ev.event_contributing_tokens == 92 and ev.event_total_mismatch == 0, "Azure stream: 92, reconciles")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
