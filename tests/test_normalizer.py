"""Phase keystone — the normalizer (single assembly point).

Run: python tests/test_normalizer.py

normalize(response, adapter) is the one call that turns a raw provider response into a stored
TokenEvent: it runs the adapter, pulls identity from the propagation context, applies the
normalizer-owned data-quality flags, and never raises into the caller (an adapter blow-up
becomes a normalization_error event, not a crash).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace, span, trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource  # noqa: E402
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


# --- 1) happy path: explicit context, clean OpenAI payload ---
ctx = new_trace(business_id="biz-1", workflow="rag", environment="prod")
ev = normalize(load("openai_chat_completions_cached_reasoning.SIMULATED.json"), OpenAIChatCompletionsAdapter(), context=ctx)
check(ev.event_id.startswith("evt-") and len(ev.event_id) > 4, "event_id is generated")
check(ev.trace_id == ctx.trace_id and ev.span_id == ctx.span_id, "identity taken from the context")
check(ev.request_correlation_id == ctx.request_correlation_id, "request_correlation_id taken from the context")
check(ev.business_id == "biz-1" and ev.workflow == "rag" and ev.environment == "prod", "cross-cutting labels propagated")
check(ev.provider == "openai" and ev.api_surface == "chat_completions", "provider fields from the adapter")
check(ev.event_contributing_tokens == 1300, "assembled event contributes 1300 (no double count)")
check(ev.data_quality_flags == [], "clean event has no flags")

# --- 2) context pulled from the active propagation context when not passed ---
with trace(business_id="biz-2", workflow="agent", environment="dev"):
    with span() as sp:
        ev2 = normalize(load("openai_responses_cached_reasoning.SIMULATED.json"), OpenAIResponsesAdapter())
check(ev2.trace_id == sp.trace_id and ev2.span_id == sp.span_id, "ambient context is used with no explicit context")
check(ev2.business_id == "biz-2" and ev2.workflow == "agent", "ambient labels propagated")

# --- 3) Bedrock cache buckets follow AWS's documented additive formula ---
ev3 = normalize(load("bedrock_converse_cache.SIMULATED.json"), BedrockConverseAdapter(), context=new_trace())
check("unverified_additivity" not in ev3.data_quality_flags, "Bedrock documented cache additivity is verified")
check(ev3.event_contributing_tokens == 2220, "Bedrock contributing total includes cache read/write")

# --- 4) missing usage -> raw_usage_missing, no fabricated quantities ---
ev4 = normalize({"id": "x"}, OpenAIResponsesAdapter(), context=new_trace())
check("raw_usage_missing" in ev4.data_quality_flags, "missing usage -> raw_usage_missing")
check(ev4.quantities == [] and ev4.event_contributing_tokens == 0, "missing usage -> no quantities, contributes 0")


# --- 5) an adapter that blows up becomes a normalization_error event (never a crash) ---
class _FakeAdapter(BaseAPISurfaceAdapter):
    provider = "fake"
    api_surface = "surface"

    def __init__(self, mode):
        self.mode = mode

    def count_input_tokens(self, request):
        return 0

    def extract_usage_from_response(self, response):
        if self.mode == "raise":
            raise RuntimeError("boom")
        q = self.build_quantity(TokenType.INPUT, 100, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)
        return NormalizedUsage(self.provider, self.api_surface, quantities=[q], provider_total_tokens=999)

    def extract_usage_from_stream_event(self, event):
        return None

    def estimate_partial_output_tokens(self, accumulated_text):
        return 0

    def reconcile_total(self, quantities, raw_total):
        return raw_total

    def classify_error(self, exc):
        return "normalization_error"


ev5 = normalize({"anything": 1}, _FakeAdapter("raise"), context=new_trace())
check("normalization_error" in ev5.data_quality_flags, "adapter exception -> normalization_error flag")
check(ev5.quantities == [], "adapter exception -> no quantities (and no crash)")

# --- 6) provider/derived mismatch flagged; extra_flags merged + de-duplicated ---
ev6 = normalize({}, _FakeAdapter("mismatch"), context=new_trace(), extra_flags=["unverified_additivity", "custom"])
check("provider_total_mismatch" in ev6.data_quality_flags, "provider total 999 != derived 100 -> provider_total_mismatch")
check(ev6.data_quality_flags.count("custom") == 1 and "custom" in ev6.data_quality_flags, "extra_flags merged")
check(all(ev6.data_quality_flags.count(f) == 1 for f in ev6.data_quality_flags), "flags are de-duplicated")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
