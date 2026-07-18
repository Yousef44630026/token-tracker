"""Extra — adapter contract methods across all providers.

Run: python tests/test_adapter_methods.py

Exercises the non-response methods every adapter must implement: stream-event extraction
(final usage -> provider_stream_final), local input counting, partial-output estimation,
error classification, total reconciliation, and the raw_usage_missing path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.models.enums import TokenType, UsageSource  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


adapters = [OpenAIResponsesAdapter(), OpenAIChatCompletionsAdapter(), BedrockConverseAdapter(), GeminiGenerateContentAdapter()]

for a in adapters:
    label = type(a).__name__
    # common contract methods
    check(a.count_input_tokens("hello world prompt") > 0, f"{label}: count_input_tokens > 0 on text")
    check(a.estimate_partial_output_tokens("a partial answer so far") > 0, f"{label}: estimate_partial_output_tokens > 0")
    check(a.classify_error(ValueError("boom")) == "normalization_error", f"{label}: classify_error -> normalization_error")
    check(a.reconcile_total([], 1300) == 1300, f"{label}: reconcile_total returns the raw total")
    # missing usage -> raw_usage_missing, no fabricated quantities
    nu = a.extract_usage_from_response({"id": "x"})
    check("raw_usage_missing" in nu.data_quality_flags and nu.quantities == [], f"{label}: missing usage -> raw_usage_missing")
    # stream event with no usage -> None
    check(a.extract_usage_from_stream_event({"id": "y"}) is None, f"{label}: stream event without usage -> None")

# --- stream-final extraction marks PROVIDER_STREAM_FINAL ---
oai_final = OpenAIChatCompletionsAdapter().extract_usage_from_stream_event(
    {"model": "gpt", "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}
)
out = next(q for q in oai_final.quantities if q.token_type == TokenType.OUTPUT)
check(out.usage_source == UsageSource.PROVIDER_STREAM_FINAL, "OpenAI stream-final -> provider_stream_final source")
check(oai_final.provider_total_tokens == 120, "OpenAI stream-final extracts provider total")

gem_final = GeminiGenerateContentAdapter().extract_usage_from_stream_event(
    {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 120}}
)
check(gem_final is not None and gem_final.provider_total_tokens == 120, "Gemini stream-final extracts usageMetadata")

bed_final = BedrockConverseAdapter().extract_usage_from_stream_event(
    {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120}}}
)
check(bed_final is not None and bed_final.provider_total_tokens == 120, "Bedrock stream-final reads metadata.usage")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
