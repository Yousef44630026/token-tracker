"""Regression (P3) — a subtotal must reference a parent that is actually present.

INV-4: a SUBTOTAL_OF quantity breaks down a parent quantity WITHIN the same event (cached_input
is part of input, reasoning is part of output). The model already requires subtotal_of to be a
non-empty string, but it never checked the named parent actually exists among the sibling
quantities. A 'dangling subtotal' (subtotal_of naming a token_type not present) is a structural
contradiction — it claims to break down something that isn't there — and must be rejected at the
model boundary, exactly like an empty subtotal_of already is. Totals are unaffected either way
(subtotals contribute 0), but the breakdown story must not silently lie.

Run: python tests/test_subtotal_referential_integrity.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, qty, add=Additivity.TOTAL_CONTRIBUTING, parent=None):
    return TokenQuantity(tt, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, add, subtotal_of=parent)


def event(quantities):
    return TokenEvent(
        event_id="e1",
        request_correlation_id="r1",
        trace_id="t",
        span_id="s",
        quantities=quantities,
        observation={"authoritative": True},
    )


# --- valid: cached_input is a subtotal of input, and input IS present ---
ok = event(
    [
        q(TokenType.INPUT, 1000),
        q(TokenType.OUTPUT, 300),
        q(TokenType.CACHED_INPUT, 800, add=Additivity.SUBTOTAL_OF, parent="input"),
    ]
)
check(len(ok.quantities) == 3, "a subtotal whose parent token_type is present is accepted")

# --- valid: reasoning is a subtotal of output, and output IS present ---
ok2 = event(
    [
        q(TokenType.INPUT, 100),
        q(TokenType.OUTPUT, 500),
        q(TokenType.REASONING, 120, add=Additivity.SUBTOTAL_OF, parent="output"),
    ]
)
check(len(ok2.quantities) == 3, "reasoning subtotal of a present output is accepted")

# --- invalid: cached_input claims to be a subtotal of 'input', but no input quantity exists ---
try:
    event(
        [
            q(TokenType.OUTPUT, 300),
            q(TokenType.CACHED_INPUT, 800, add=Additivity.SUBTOTAL_OF, parent="input"),
        ]
    )
    check(False, "dangling subtotal (parent absent) should raise, but it was accepted")
except ValueError:
    check(True, "dangling subtotal (parent token_type absent) is rejected at the event boundary")

# --- invalid: subtotal_of names a token_type that simply isn't in the event ---
try:
    event(
        [
            q(TokenType.INPUT, 100),
            q(TokenType.OUTPUT, 300),
            q(TokenType.AUDIO_INPUT, 40, add=Additivity.SUBTOTAL_OF, parent="video_input"),
        ]
    )
    check(False, "subtotal_of a token_type not present should raise, but it was accepted")
except ValueError:
    check(True, "subtotal_of a token_type not present in the event is rejected")

# --- totals are never affected by this (subtotals contribute 0), but integrity is enforced ---
check(ok.event_contributing_tokens == 1300, "valid event still totals input+output only (subtotal contributes 0)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
