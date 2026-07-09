"""Regression (INV-4) — an INDEPENDENT quantity must not name a subtotal parent.

TokenQuantity already rejects a SUBTOTAL_OF quantity with no ``subtotal_of`` parent. The
CONVERSE was missing: a quantity whose overlap is INDEPENDENT while it ALSO carries a
non-null ``subtotal_of`` is a self-contradiction. ``subtotal_of`` names the parent a count is
a breakdown of; an INDEPENDENT count is, by definition, not a breakdown of anything.

Why this lets a number lie:
  - included_in_total is (INDEPENDENT and VERIFIED and known) -> such a quantity IS summed
    into the headline total, yet it simultaneously claims to be contained inside a sibling.
  - the event-level dangling-subtotal check only fires for overlap == SUBTOTAL_OF, so an
    INDEPENDENT quantity can name a parent token_type that isn't even present and still be
    summed — the referential-integrity guard is bypassed entirely.

The real pipeline never produces this (assign_additivity pairs subtotal_of with a subtotal,
and _default_overlap resolves any non-null subtotal_of to SUBTOTAL_OF). It is only reachable
from a hand-edited / corrupt stored row read back through from_dict, or from buggy direct
construction that forces overlap=INDEPENDENT. Either way the model boundary must reject it,
exactly as it already rejects the empty-subtotal_of case.

Run: python tests/test_independent_subtotal_contradiction.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import (  # noqa: E402
    Additivity,
    Overlap,
    PrecisionLevel,
    TokenType,
    Trust,
    UsageSource,
)
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# --- the contradiction: INDEPENDENT overlap explicitly forced, yet subtotal_of names a parent ---
try:
    TokenQuantity(
        token_type=TokenType.INPUT,
        quantity=100,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
        overlap=Overlap.INDEPENDENT,
        trust=Trust.VERIFIED,
        subtotal_of="output",
    )
    check(
        False,
        "INDEPENDENT overlap with a non-null subtotal_of should raise, but was accepted",
    )
except ValueError:
    check(
        True,
        "INDEPENDENT overlap with a non-null subtotal_of is rejected (converse of the subtotal guard)",
    )

# --- the same contradiction arriving from a corrupt/hand-edited stored row via from_dict ---
corrupt_row = {
    "token_type": "input",
    "quantity": 100,
    "precision_level": "exact",
    "usage_source": "provider_response",
    "additivity": "total_contributing",
    "overlap": "independent",
    "trust": "verified",
    "aggregation_mode": "sum",
    "token_role": None,
    "subtotal_of": "output",
    "unknown_reason": None,
    "metadata": {},
}
try:
    TokenQuantity.from_dict(corrupt_row)
    check(
        False,
        "from_dict of an independent+subtotal_of row should raise, but was accepted",
    )
except ValueError:
    check(True, "from_dict rejects a corrupt independent+subtotal_of stored row")

# --- BACKWARD COMPAT: a legacy row WITHOUT an overlap key but WITH subtotal_of still loads.
# _default_overlap resolves the non-null subtotal_of to SUBTOTAL_OF, so it is NOT a contradiction. ---
legacy_subtotal = {
    "token_type": "cached_input",
    "quantity": 80,
    "precision_level": "exact",
    "usage_source": "provider_response",
    "additivity": "subtotal_of",
    "subtotal_of": "input",
    "metadata": {},
}
q_legacy = TokenQuantity.from_dict(legacy_subtotal)
check(
    q_legacy.overlap == Overlap.SUBTOTAL_OF and q_legacy.subtotal_of == "input",
    "legacy subtotal row (no overlap key) still loads and defaults overlap to SUBTOTAL_OF",
)

# --- BACKWARD COMPAT: a plain independent row with no subtotal_of still loads and is summed ---
plain = TokenQuantity(
    token_type=TokenType.INPUT,
    quantity=100,
    precision_level=PrecisionLevel.EXACT,
    usage_source=UsageSource.PROVIDER_RESPONSE,
    additivity=Additivity.TOTAL_CONTRIBUTING,
)
check(
    plain.overlap == Overlap.INDEPENDENT and plain.subtotal_of is None and plain.quantity_in_total == 100,
    "a plain independent quantity (no subtotal_of) is unaffected and still contributes",
)

# --- the existing symmetric guard still holds: SUBTOTAL_OF requires a subtotal_of parent ---
try:
    TokenQuantity(
        token_type=TokenType.CACHED_INPUT,
        quantity=80,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.SUBTOTAL_OF,
        overlap=Overlap.SUBTOTAL_OF,
        trust=Trust.VERIFIED,
        subtotal_of=None,
    )
    check(False, "SUBTOTAL_OF overlap with no subtotal_of should still raise")
except ValueError:
    check(
        True,
        "existing guard intact: SUBTOTAL_OF overlap still requires a subtotal_of parent",
    )

# --- a legitimate SUBTOTAL_OF quantity is still accepted ---
good_sub = TokenQuantity(
    token_type=TokenType.CACHED_INPUT,
    quantity=80,
    precision_level=PrecisionLevel.EXACT,
    usage_source=UsageSource.PROVIDER_RESPONSE,
    additivity=Additivity.SUBTOTAL_OF,
    subtotal_of="input",
)
check(
    good_sub.overlap == Overlap.SUBTOTAL_OF and good_sub.quantity_in_total == 0,
    "a real subtotal is still accepted and contributes 0",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
