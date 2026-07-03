"""Extra — robustness: malformed / junk responses never crash the tracker.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_robustness_malformed.py

Every adapter, driven through the normalizer, must turn garbage (None, wrong types, empty or
partial usage) into a flagged TokenEvent that contributes 0 — never an exception, never a
fabricated quantity.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


ADAPTERS = [
    OpenAIResponsesAdapter(),
    OpenAIChatCompletionsAdapter(),
    AzureOpenAIChatCompletionsAdapter(),
    BedrockConverseAdapter(),
    GeminiGenerateContentAdapter(),
    AnthropicMessagesAdapter(),
    BedrockInvokeModelAdapter(),
]

# junk that carries no usable usage at all
JUNK = [
    None,
    {},
    "garbage",
    123,
    4.5,
    [],
    (),
    {"foo": "bar"},
    {"usage": None},
    {"usage": {}},
    {"usageMetadata": {}},
    {"ResponseMetadata": {}},
    {"ResponseMetadata": {"HTTPHeaders": {}}},
]

for adapter in ADAPTERS:
    label = type(adapter).__name__
    crashed = False
    all_zero = True
    all_flagged = True
    for r in JUNK:
        try:
            ev = normalize(r, adapter, context=new_trace())
        except Exception as exc:  # noqa: BLE001
            crashed = True
            print(f"  [crash] {label} on {r!r}: {type(exc).__name__}")
            break
        if not isinstance(ev, TokenEvent):
            all_flagged = False
        if ev.event_contributing_tokens != 0:
            all_zero = False
        if not ({"raw_usage_missing", "normalization_error"} & set(ev.data_quality_flags)):
            all_flagged = False
    check(not crashed, f"{label}: never crashes on junk input")
    check(all_zero, f"{label}: junk input contributes 0 tokens")
    check(all_flagged, f"{label}: junk input is flagged (raw_usage_missing / normalization_error)")


# --- partial usage: the available quantity is captured, no crash ---
def out_q(ev):
    return next((q for q in ev.quantities if q.token_type == TokenType.OUTPUT), None)


def in_q(ev):
    return next((q for q in ev.quantities if q.token_type == TokenType.INPUT), None)


oa_in_only = normalize({"usage": {"prompt_tokens": 100}}, OpenAIChatCompletionsAdapter(), context=new_trace())
check(in_q(oa_in_only) is not None and oa_in_only.event_contributing_tokens == 100, "OpenAI input-only usage -> 100")
oa_out_only = normalize({"usage": {"completion_tokens": 50}}, OpenAIChatCompletionsAdapter(), context=new_trace())
check(out_q(oa_out_only) is not None and oa_out_only.event_contributing_tokens == 50, "OpenAI output-only usage -> 50")
an_in_only = normalize({"usage": {"input_tokens": 70}}, AnthropicMessagesAdapter(), context=new_trace())
check(an_in_only.event_contributing_tokens == 70, "Anthropic input-only usage -> 70")
bim_in_only = normalize(
    {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "33"}}}, BedrockInvokeModelAdapter(), context=new_trace()
)
check(bim_in_only.event_contributing_tokens == 33, "InvokeModel input-only header -> 33")

# --- extra unknown fields are ignored, not fatal ---
noisy = {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "mystery": {"x": 1}}, "extra": [1, 2]}
ev = normalize(noisy, OpenAIChatCompletionsAdapter(), context=new_trace())
check(ev.event_contributing_tokens == 120, "unknown extra fields ignored, totals still correct")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
