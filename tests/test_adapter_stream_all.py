"""Extra — stream extraction + contract methods for the remaining adapters.

Run: python tests/test_adapter_stream_all.py

Covers Azure (Responses + Chat), Anthropic Messages, and Bedrock InvokeModel — the adapters
not exercised by test_adapter_methods — for stream-final extraction and the common contract.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.models.enums import TokenType, UsageSource  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def out_q(usage):
    return next((q for q in usage.quantities if q.token_type == TokenType.OUTPUT), None)


adapters = [
    AzureOpenAIResponsesAdapter(),
    AzureOpenAIChatCompletionsAdapter(),
    AnthropicMessagesAdapter(),
    BedrockInvokeModelAdapter(),
]

# --- common contract on every adapter ---
for a in adapters:
    label = type(a).__name__
    check(a.count_input_tokens("a prompt") > 0, f"{label}: count_input_tokens > 0")
    check(a.estimate_partial_output_tokens("partial") > 0, f"{label}: estimate_partial_output_tokens > 0")
    check(a.classify_error(RuntimeError("x")) == "normalization_error", f"{label}: classify_error")
    check(a.reconcile_total([], 7) == 7, f"{label}: reconcile_total passes the raw total")
    check(a.extract_usage_from_stream_event({"nothing": 1}) is None, f"{label}: stream w/o usage -> None")

# --- Azure stream-final (OpenAI usage shape, azure provider label) ---
az = AzureOpenAIChatCompletionsAdapter().extract_usage_from_stream_event(
    {"model": "gpt-4o", "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
)
check(az.provider == "azure_openai" and az.provider_total_tokens == 120, "Azure stream-final: provider + total")
check(out_q(az).usage_source == UsageSource.PROVIDER_STREAM_FINAL, "Azure stream-final: provider_stream_final source")

azr = AzureOpenAIResponsesAdapter().extract_usage_from_stream_event(
    {"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
)
check(azr is not None and azr.provider_total_tokens == 120, "Azure Responses stream-final extracts usage")

# --- Anthropic stream-final (no total) ---
an = AnthropicMessagesAdapter().extract_usage_from_stream_event({"usage": {"input_tokens": 100, "output_tokens": 20}})
check(an is not None and an.provider_total_tokens is None, "Anthropic stream-final: no provider total")
check(out_q(an).quantity == 20 and out_q(an).usage_source == UsageSource.PROVIDER_STREAM_FINAL, "Anthropic stream-final output")

# --- Bedrock InvokeModel stream-final (header counts) ---
bim = BedrockInvokeModelAdapter().extract_usage_from_stream_event(
    {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "100", "x-amzn-bedrock-output-token-count": "20"}}}
)
check(bim is not None and out_q(bim).quantity == 20, "InvokeModel stream-final reads header counts")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
