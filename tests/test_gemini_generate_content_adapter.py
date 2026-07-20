"""Phase 10 — Gemini Generate Content adapter (thinking = total_contributing). (INV-4)

Run: python tests/test_gemini_generate_content_adapter.py

SIMULATED fixture (no API credit to capture a real payload). For Gemini, thinking and tool
result input are additive; cachedContent is a subtotal of prompt input.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, DataQualityFlag, TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def by_type(usage, tt):
    return next((q for q in usage.quantities if q.token_type == tt), None)


with open(os.path.join(FIXTURES, "gemini_generate_content_thinking.SIMULATED.json"), encoding="utf-8") as f:
    fx = json.load(f)
check(fx.get("_SIMULATED") is True, "fixture is explicitly marked SIMULATED")

usage = GeminiGenerateContentAdapter().extract_usage_from_response(fx["response"])

inp = by_type(usage, TokenType.INPUT)
out = by_type(usage, TokenType.OUTPUT)
cached = by_type(usage, TokenType.CACHED_INPUT)
thinking = by_type(usage, TokenType.THINKING)

check(inp.quantity == 1000 and inp.additivity == Additivity.TOTAL_CONTRIBUTING, "prompt -> input total_contributing")
check(out.quantity == 300 and out.additivity == Additivity.TOTAL_CONTRIBUTING, "candidates -> output total_contributing")
check(
    cached is not None and cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input",
    "cachedContent -> subtotal_of input",
)
check(thinking is not None and thinking.quantity == 250, "thoughts -> thinking extracted")
check(thinking.additivity == Additivity.TOTAL_CONTRIBUTING, "thinking is total_contributing (added on top)")
check(thinking.quantity_in_total == 250, "thinking contributes its tokens")
check(cached.quantity_in_total == 0, "cachedContent contributes 0")

check(usage.provider_total_tokens == 1550, "provider_total_tokens == 1550")

event = TokenEvent(
    event_id="evt-gemini",
    request_correlation_id="r-g",
    trace_id="t-1",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
    observation={"authoritative": True},
)
check(event.event_contributing_tokens == 1550, "contributing == input+output+thinking == 1550")
check(event.event_contributing_tokens == event.provider_total_tokens, "reconciles to provider total (no mismatch)")

flags = normalizer_flags(usage.quantities, usage.provider_total_tokens)
check("provider_total_mismatch" not in flags, "no provider_total_mismatch (thinking reconciled)")
check("unverified_additivity" not in flags, "Gemini additivity is verified, not unverified")

# Google documents totalTokenCount as prompt + candidates + tool-use prompt + thoughts.
tool_usage = GeminiGenerateContentAdapter().extract_usage_from_response(
    {
        "modelVersion": "gemini-tool-audit",
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 20,
            "toolUsePromptTokenCount": 30,
            "thoughtsTokenCount": 10,
            "totalTokenCount": 160,
        },
    }
)
tool_input = next((q for q in tool_usage.quantities if q.token_role == "tool_result"), None)
check(
    tool_input is not None and tool_input.token_type == TokenType.INPUT and tool_input.quantity == 30,
    "tool-use results are retained as an independent input quantity",
)
tool_event = TokenEvent(
    event_id="evt-gemini-tool",
    request_correlation_id="r-g-tool",
    trace_id="t-tool",
    span_id="s-tool",
    provider=tool_usage.provider,
    api_surface=tool_usage.api_surface,
    quantities=tool_usage.quantities,
    provider_total_tokens=tool_usage.provider_total_tokens,
    observation={"authoritative": True},
)
check(tool_event.event_contributing_tokens == 160, "tool-use input reconciles to the provider total")
check("provider_total_mismatch" not in tool_event.data_quality_flags, "tool-use calls do not undercount silently")


class SDKUsage:
    prompt_token_count = 100
    candidates_token_count = 20
    cached_content_token_count = 5
    thoughts_token_count = 10
    tool_use_prompt_token_count = 30
    total_token_count = 160
    prompt_tokens_details = []
    candidates_tokens_details = []

    def model_dump(self):
        return {
            "prompt_token_count": self.prompt_token_count,
            "candidates_token_count": self.candidates_token_count,
            "cached_content_token_count": self.cached_content_token_count,
            "thoughts_token_count": self.thoughts_token_count,
            "tool_use_prompt_token_count": self.tool_use_prompt_token_count,
            "total_token_count": self.total_token_count,
            "prompt_tokens_details": [],
            "candidates_tokens_details": [],
        }


class SDKResponse:
    usage_metadata = SDKUsage()
    model_version = "gemini-sdk"


sdk_event = normalize(SDKResponse(), GeminiGenerateContentAdapter(), context=new_trace())
check(sdk_event.event_contributing_tokens == 160, "Google SDK snake_case usage is counted exactly")
check(sdk_event.provider_total_tokens == 160, "Google SDK total_token_count is retained")
check(sdk_event.model == "gemini-sdk", "Google SDK model_version is retained")
check(DataQualityFlag.RAW_USAGE_MISSING.value not in sdk_event.data_quality_flags, "Google SDK usage is not mistaken for missing")
check(
    DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value not in sdk_event.data_quality_flags,
    "known Google SDK field aliases do not create false drift",
)

partial_stream_chunk = {
    "usageMetadata": {"promptTokenCount": 100},
    "candidates": [{"content": {"parts": [{"text": "partial"}]}}],
}
partial_stream_usage = GeminiGenerateContentAdapter().extract_usage_from_stream_event(partial_stream_chunk)
check(
    partial_stream_usage is not None and partial_stream_usage.stream_terminal is False,
    "Gemini usage without a finish marker is never promoted to terminal usage",
)
interrupted_stream = consume_stream(
    events=[partial_stream_chunk],
    adapter=GeminiGenerateContentAdapter(),
    context=new_trace(),
)
check(interrupted_stream.observation.get("status") == "incomplete", "truncated Gemini usage remains incomplete")
check(
    DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value in interrupted_stream.data_quality_flags,
    "truncated Gemini usage carries an explicit missing-final-usage flag",
)

terminal_stream_usage = GeminiGenerateContentAdapter().extract_usage_from_stream_event(
    {
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 20,
            "totalTokenCount": 120,
        },
        "candidates": [{"finishReason": "STOP"}],
    }
)
check(
    terminal_stream_usage is not None and terminal_stream_usage.stream_terminal is True,
    "Gemini usage with a finish marker is accepted as terminal",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
