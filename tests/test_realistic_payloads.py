"""Near-real adapter tests — full provider response payloads (SIMULATED but realistic).

Run: python tests/test_realistic_payloads.py

These fixtures mirror the COMPLETE documented response shape of each provider (id, choices /
content / metrics / ResponseMetadata, the full usage object), not just a stripped usage blob.
So this is as close to a real captured-payload test as possible without API credit: it proves
each adapter digs usage out of a realistic structure and that additivity/no-double-count holds.
Swap a fixture for a genuinely captured payload and these become ground-truth tests verbatim.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, Overlap, TokenType, Trust  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

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


# ===== OpenAI Chat Completions (non-reasoning model, cache present) =====
ev = normalize(load("openai_chat_full.SIMULATED.json"), OpenAIChatCompletionsAdapter(), context=new_trace())
check(ev.model == "gpt-4o-2024-08-06", "OpenAI Chat: model pulled from the full payload")
check(q(ev, TokenType.INPUT).quantity == 1200 and q(ev, TokenType.OUTPUT).quantity == 350, "OpenAI Chat: input/output extracted")
check(q(ev, TokenType.CACHED_INPUT).additivity == Additivity.SUBTOTAL_OF, "OpenAI Chat: cached_tokens -> subtotal")
check(q(ev, TokenType.REASONING) is None, "OpenAI Chat: reasoning_tokens=0 creates no quantity")
check(ev.event_contributing_tokens == 1550 and ev.event_total_mismatch == 0, "OpenAI Chat: 1550, reconciles")

# ===== OpenAI Responses (reasoning model) =====
ev = normalize(load("openai_responses_full.SIMULATED.json"), OpenAIResponsesAdapter(), context=new_trace())
check(q(ev, TokenType.INPUT).quantity == 2048 and q(ev, TokenType.OUTPUT).quantity == 900, "OpenAI Responses: input/output extracted")
check(
    q(ev, TokenType.REASONING).additivity == Additivity.SUBTOTAL_OF and q(ev, TokenType.REASONING).subtotal_of == "output",
    "OpenAI Responses: reasoning -> subtotal_of output",
)
check(q(ev, TokenType.CACHED_INPUT).quantity_in_total == 0, "OpenAI Responses: cached contributes 0")
check(ev.event_contributing_tokens == 2948 and ev.event_total_mismatch == 0, "OpenAI Responses: 2948, reconciles")

# ===== Anthropic Messages (both cache fields, no total) =====
ev = normalize(load("anthropic_messages_full.SIMULATED.json"), AnthropicMessagesAdapter(), context=new_trace())
check(ev.model == "claude-3-5-sonnet-20241022", "Anthropic: model pulled from the full payload")
check(q(ev, TokenType.CACHED_INPUT).additivity == Additivity.TOTAL_CONTRIBUTING, "Anthropic: cache_read contributes")
check(q(ev, TokenType.CACHE_CREATION_INPUT).additivity == Additivity.TOTAL_CONTRIBUTING, "Anthropic: cache_creation contributes")
check(ev.provider_total_tokens is None and ev.event_total_mismatch is None, "Anthropic: no provider total")
check(ev.event_contributing_tokens == 3144 and "unverified_additivity" not in ev.data_quality_flags, "Anthropic: 3144 with verified cache")

# ===== Gemini (thinking added on top) =====
ev = normalize(load("gemini_generate_full.SIMULATED.json"), GeminiGenerateContentAdapter(), context=new_trace())
check(ev.model == "gemini-2.5-pro", "Gemini: modelVersion pulled from the full payload")
check(
    q(ev, TokenType.THINKING).additivity == Additivity.TOTAL_CONTRIBUTING and q(ev, TokenType.THINKING).quantity_in_total == 300,
    "Gemini: thinking contributes 300",
)
check(q(ev, TokenType.CACHED_INPUT).quantity_in_total == 0, "Gemini: cachedContent contributes 0")
check(ev.event_contributing_tokens == 2600 and ev.event_total_mismatch == 0, "Gemini: 2600 == input+output+thinking, reconciles")

# ===== Bedrock Converse (cacheWrite=0 must not create a quantity; no modelId in body) =====
ev = normalize(load("bedrock_converse_full.SIMULATED.json"), BedrockConverseAdapter(), context=new_trace())
check(ev.model is None, "Bedrock Converse: no modelId in the response body -> model None")
check(q(ev, TokenType.CACHED_INPUT).additivity == Additivity.TOTAL_CONTRIBUTING, "Bedrock Converse: cacheRead contributes")
check(
    q(ev, TokenType.CACHED_INPUT).overlap == Overlap.INDEPENDENT
    and q(ev, TokenType.CACHED_INPUT).trust == Trust.VERIFIED
    and q(ev, TokenType.CACHED_INPUT).subtotal_of is None,
    "Bedrock Converse: cacheRead is an independent verified input bucket",
)
check(q(ev, TokenType.CACHE_CREATION_INPUT) is None, "Bedrock Converse: cacheWrite=0 creates no quantity")
check(ev.event_contributing_tokens == 2380 and ev.event_total_mismatch == 0, "Bedrock Converse: 2380, reconciles")
check("unverified_additivity" not in ev.data_quality_flags, "Bedrock Converse: documented cache semantics are verified")

# ===== Bedrock InvokeModel (token counts from headers in ResponseMetadata) =====
ev = normalize(load("bedrock_invoke_model_full.SIMULATED.json"), BedrockInvokeModelAdapter(), context=new_trace())
check(
    q(ev, TokenType.INPUT).quantity == 950 and q(ev, TokenType.OUTPUT).quantity == 275,
    "InvokeModel: counts read from ResponseMetadata headers",
)
check(ev.event_contributing_tokens == 1225 and ev.provider_total_tokens is None, "InvokeModel: 1225, no provider total")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
