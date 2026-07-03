"""Extra — additional providers: Mistral, Cohere, Voyage rerank.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_more_providers.py

Mistral is OpenAI-compatible; Cohere reports usage.tokens/billed_units; Voyage rerank reports
usage.total_tokens -> a single rerank_input quantity. All registered total_contributing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.cohere_chat_adapter import CohereChatAdapter  # noqa: E402
from tracker.adapters.mistral_chat_adapter import MistralChatAdapter  # noqa: E402
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter  # noqa: E402
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


# ===== Mistral (OpenAI-compatible) =====
ev = normalize(load("mistral_chat_full.SIMULATED.json"), MistralChatAdapter(), context=new_trace())
check(ev.provider == "mistral" and ev.model == "mistral-large-latest", "Mistral: provider + model")
check(q(ev, TokenType.INPUT).quantity == 480 and q(ev, TokenType.OUTPUT).quantity == 120, "Mistral: input/output extracted")
check(q(ev, TokenType.INPUT).additivity == Additivity.TOTAL_CONTRIBUTING, "Mistral: input total_contributing (registered, not fail-closed)")
check(ev.event_contributing_tokens == 600 and ev.event_total_mismatch == 0, "Mistral: 600, reconciles")
check(ev.data_quality_flags == [], "Mistral: no unverified flag")

# ===== Cohere (usage.tokens preferred over billed_units) =====
ev = normalize(load("cohere_chat_full.SIMULATED.json"), CohereChatAdapter(), context=new_trace())
check(ev.provider == "cohere", "Cohere: provider")
check(q(ev, TokenType.INPUT).quantity == 310 and q(ev, TokenType.OUTPUT).quantity == 90, "Cohere: raw tokens used (310/90, not billed 300)")
check(ev.event_contributing_tokens == 400, "Cohere: 310 + 90 == 400")
check(ev.provider_total_tokens is None and ev.event_total_mismatch is None, "Cohere: no provider total")

# --- Cohere regression: a PRESENT-but-empty tokens object must NOT fall back to
# billed_units (a present {} is not the same as an absent key) ---
ev = normalize(
    {"model": "command-r-plus", "usage": {"tokens": {}, "billed_units": {"input_tokens": 300, "output_tokens": 90}}},
    CohereChatAdapter(),
    context=new_trace(),
)
check(
    q(ev, TokenType.INPUT) is None and q(ev, TokenType.OUTPUT) is None,
    "Cohere: present-but-empty tokens{} is used as-is, does NOT silently fall back to billed_units",
)

# ===== Voyage rerank =====
ev = normalize(load("voyage_rerank_full.SIMULATED.json"), VoyageRerankAdapter(), context=new_trace())
rr = q(ev, TokenType.RERANK_INPUT)
check(rr is not None and rr.quantity == 1500, "Voyage: rerank_input from total_tokens (1500)")
check(rr.additivity == Additivity.TOTAL_CONTRIBUTING, "Voyage: rerank_input total_contributing")
check(q(ev, TokenType.INPUT) is None and q(ev, TokenType.OUTPUT) is None, "Voyage: no input/output (rerank has neither)")
check(ev.event_contributing_tokens == 1500 and ev.event_total_mismatch == 0, "Voyage: 1500, reconciles")

# raw_usage_missing path
empty = normalize({"object": "list", "data": []}, VoyageRerankAdapter(), context=new_trace())
check("raw_usage_missing" in empty.data_quality_flags, "Voyage: missing usage -> raw_usage_missing")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
