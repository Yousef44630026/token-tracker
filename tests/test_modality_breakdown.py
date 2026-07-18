"""Extra — multimodal token breakdown (audio / image / video attribution).

Run: python tests/test_modality_breakdown.py

Audio (OpenAI) and per-modality (Gemini promptTokensDetails) counts are SUBTOTALS of input /
output: they attribute how much of the prompt/completion was each modality, WITHOUT changing
the total (no double count). The total still reconciles to the provider total.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
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


# ===== OpenAI audio model: audio_tokens broken out of input/output =====
ev = normalize(load("openai_chat_audio.SIMULATED.json"), OpenAIChatCompletionsAdapter(), context=new_trace())
ai, ao = q(ev, TokenType.AUDIO_INPUT), q(ev, TokenType.AUDIO_OUTPUT)
check(ai is not None and ai.quantity == 300, "OpenAI: audio_input broken out (300)")
check(ao is not None and ao.quantity == 150, "OpenAI: audio_output broken out (150)")
check(ai.additivity == Additivity.SUBTOTAL_OF and ai.subtotal_of == "input", "audio_input is subtotal_of input")
check(ao.additivity == Additivity.SUBTOTAL_OF and ao.subtotal_of == "output", "audio_output is subtotal_of output")
check(ai.quantity_in_total == 0 and ao.quantity_in_total == 0, "audio subtotals contribute 0 (no double count)")
check(ev.event_contributing_tokens == 700 and ev.event_total_mismatch == 0, "OpenAI audio: total still 700, reconciles")

# ===== Gemini multimodal: per-modality breakdown of the prompt =====
ev = normalize(load("gemini_multimodal.SIMULATED.json"), GeminiGenerateContentAdapter(), context=new_trace())
img, aud = q(ev, TokenType.IMAGE_INPUT), q(ev, TokenType.AUDIO_INPUT)
check(img is not None and img.quantity == 1000 and img.subtotal_of == "input", "Gemini: image_input broken out (1000), subtotal of input")
check(aud is not None and aud.quantity == 200 and aud.subtotal_of == "input", "Gemini: audio_input broken out (200), subtotal of input")
check(q(ev, TokenType.INPUT).quantity == 2000, "Gemini: full prompt count kept (2000)")
check(img.quantity_in_total == 0 and aud.quantity_in_total == 0, "Gemini: modality subtotals contribute 0")
check(ev.event_contributing_tokens == 2400 and ev.event_total_mismatch == 0, "Gemini multimodal: total still 2400, reconciles")

# the TEXT modality is NOT broken out into a separate quantity (it's the bulk of input)
text_modality_qs = [x for x in ev.quantities if x.token_type in (TokenType.INPUT,) and x.subtotal_of == "input"]
check(text_modality_qs == [], "Gemini: TEXT modality not duplicated as a subtotal")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
