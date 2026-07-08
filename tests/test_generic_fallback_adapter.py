"""P0 hardening — generic fallback adapter: open capture, closed counting (INV-4 / INV-6).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_generic_fallback_adapter.py

Today an unknown provider dies in ``create_adapter`` with a ValueError: the observed call is
LOST — the one remaining way usage could vanish without a flag. The fallback adapter closes
that: it captures whatever usage the payload really carries (recognized common key spellings
only, never invented counts), and the central additivity table's fail-closed default makes
every captured quantity ``unverified`` — present in the audit trail, contributing 0 until a
dedicated adapter encodes the provider's real additivity truth.

``create_adapter`` stays strict (unchanged contract); ``create_adapter_with_fallback`` is the
explicit opt-in used by capture paths that must never drop a call.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.generic_fallback_adapter import GenericFallbackAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.registry import create_adapter, create_adapter_with_fallback  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()

# --- 1. Resolution: strict stays strict; fallback resolves anything -----------------------
try:
    create_adapter("groq", "chat_completions")
    strict_raised = False
except ValueError:
    strict_raised = True
check(strict_raised, "create_adapter stays strict: unknown provider still raises (unchanged contract)")

known = create_adapter_with_fallback("openai", "chat_completions")
check(
    type(known) is OpenAIChatCompletionsAdapter,
    "create_adapter_with_fallback returns the DEDICATED adapter when one exists",
)

fb = create_adapter_with_fallback("groq", "chat_completions")
check(isinstance(fb, GenericFallbackAdapter), "unknown provider resolves to the generic fallback")
check(fb.provider == "groq" and fb.api_surface == "chat_completions", "fallback stamps the real provider/surface")

# --- 2. OpenAI-style usage keys: captured, unverified, contributing 0 ---------------------
OPENAI_STYLE = {
    "model": "groq-llama-4",
    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
}
usage = fb.extract_usage_from_response(OPENAI_STYLE)
by_type = {q.token_type: q for q in usage.quantities}
check(set(by_type) == {TokenType.INPUT, TokenType.OUTPUT}, "recognizes prompt/completion_tokens as input/output")
check(by_type[TokenType.INPUT].quantity == 100 and by_type[TokenType.OUTPUT].quantity == 50, "captures the real counts")
check(
    all(q.additivity == Additivity.UNVERIFIED for q in usage.quantities),
    "every captured quantity is UNVERIFIED (fail-closed central table, INV-4)",
)
check(
    all(q.precision_level == PrecisionLevel.EXACT for q in usage.quantities),
    "counts the provider stated are EXACT-but-unverified (precision is not trust)",
)
check(usage.provider_total_tokens == 150, "raw provider total passes through")
check(usage.model == "groq-llama-4", "model is read from the payload")

ev = normalize(OPENAI_STYLE, fb)
check(ev.event_contributing_tokens == 0, "closed counting: the event contributes 0 to totals")
check("unverified_additivity" in ev.data_quality_flags, "and carries the unverified_additivity flag")
check(ev.provider == "groq", "the event names the real provider for the audit trail")

# --- 3. Other common spellings ------------------------------------------------------------
anthropic_style = {"usage": {"input_tokens": 7, "output_tokens": 3}}
by_type = {q.token_type: q for q in fb.extract_usage_from_response(anthropic_style).quantities}
check(
    by_type[TokenType.INPUT].quantity == 7 and by_type[TokenType.OUTPUT].quantity == 3,
    "recognizes input_tokens/output_tokens spelling",
)

gemini_style = {"usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4, "totalTokenCount": 15}}
g_usage = fb.extract_usage_from_response(gemini_style)
by_type = {q.token_type: q for q in g_usage.quantities}
check(
    by_type[TokenType.INPUT].quantity == 11 and by_type[TokenType.OUTPUT].quantity == 4,
    "recognizes usageMetadata camelCase spelling",
)
check(g_usage.provider_total_tokens == 15, "and its totalTokenCount")

# --- 4. Never invent: unknown keys are preserved raw, not turned into token types ----------
weird = {"usage": {"prompt_tokens": 5, "weird_tokens": 9}}
w_usage = fb.extract_usage_from_response(weird)
check(
    {q.token_type for q in w_usage.quantities} == {TokenType.INPUT},
    "an unrecognized usage key never becomes a token type (no invented counts)",
)
check(w_usage.raw_usage == {"prompt_tokens": 5, "weird_tokens": 9}, "the full raw usage object is preserved for audit")

# --- 5. No usage at all: flagged, never guessed -------------------------------------------
no_usage = fb.extract_usage_from_response({"model": "m", "choices": []})
check(no_usage.quantities == [], "no usage object -> no quantities")
check("raw_usage_missing" in no_usage.data_quality_flags, "and the raw_usage_missing flag")
ev_missing = normalize({"model": "m"}, fb)
check(ev_missing.event_contributing_tokens == 0, "a usage-less event contributes 0")

# --- 6. Streaming: usage-less events yield None; a final usage chunk is extracted ----------
check(fb.extract_usage_from_stream_event({"delta": "hi"}) is None, "stream chunk without usage -> None")
final_chunk = {"usage": {"prompt_tokens": 2, "completion_tokens": 1}}
stream_usage = fb.extract_usage_from_stream_event(final_chunk)
check(
    stream_usage is not None and {q.token_type for q in stream_usage.quantities} == {TokenType.INPUT, TokenType.OUTPUT},
    "stream chunk WITH usage is extracted like a response",
)

sys.exit(check.report("RESULT test_generic_fallback_adapter"))
