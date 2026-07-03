"""Phase 3 — additivity must not double-count (INV-4).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_additivity_no_double_count.py

An OpenAI-style response carries input + output (both total_contributing), plus
cached_input (subtotal_of input) and reasoning (subtotal_of output). The contributing
total must equal input + output ONLY — the cached/reasoning subtotals contribute 0, so
sum(quantity_in_total) == provider_total_tokens with no double counting.

Additivity is assigned by the per-provider table (INV-4: never inferred from the type
string), exercised here via normalization.additivity.assign_additivity.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.additivity import assign_additivity  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def q(token_type: TokenType, quantity: int) -> TokenQuantity:
    additivity, subtotal_of = assign_additivity("openai", "responses", token_type)
    return TokenQuantity(
        token_type=token_type,
        quantity=quantity,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=additivity,
        subtotal_of=subtotal_of,
    )


# input=1000 (of which 800 cached), output=300 (of which 250 reasoning)
inp = q(TokenType.INPUT, 1000)
out = q(TokenType.OUTPUT, 300)
cached = q(TokenType.CACHED_INPUT, 800)
reasoning = q(TokenType.REASONING, 250)

event = TokenEvent(
    event_id="evt-1",
    request_correlation_id="rcid-1",
    trace_id="t-1",
    span_id="s-1",
    provider="openai",
    api_surface="responses",
    quantities=[inp, out, cached, reasoning],
    provider_total_tokens=1300,  # input + output, NOT + cached + reasoning
)

# --- INV-4 assignment is per the OpenAI truth table ---
check(inp.additivity == Additivity.TOTAL_CONTRIBUTING, "input -> total_contributing")
check(out.additivity == Additivity.TOTAL_CONTRIBUTING, "output -> total_contributing")
check(
    cached.additivity == Additivity.SUBTOTAL_OF and cached.subtotal_of == "input",
    "cached_input -> subtotal_of input",
)
check(
    reasoning.additivity == Additivity.SUBTOTAL_OF and reasoning.subtotal_of == "output",
    "reasoning -> subtotal_of output",
)

# --- the subtotals contribute 0 ---
check(cached.quantity_in_total == 0, "cached_input contributes 0")
check(reasoning.quantity_in_total == 0, "reasoning contributes 0")

# --- no double count ---
total = sum(x.quantity_in_total for x in event.quantities)
check(total == 1300, f"sum(quantity_in_total) == 1300 (got {total})")
check(event.event_contributing_tokens == 1300, "event_contributing_tokens == 1300")
check(total == event.provider_total_tokens, "contributing total == provider_total_tokens")
check(event.event_total_mismatch == 0, "no provider/derived mismatch")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
