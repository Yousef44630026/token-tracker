"""P1/P5 — additivity's two orthogonal axes are explicit: overlap (structural) x trust.

The flat `additivity` enum conflated two independent questions:
  - overlap: is this count already contained within another (a subtotal)? -> structural
  - trust:   is its additivity confirmed enough to sum?                     -> verification
TokenQuantity now exposes both as first-class derived axes, and included_in_total is stated in
terms of them (independent AND verified AND known). `additivity` remains the single stored field
(unchanged wire format), so this is additive and non-breaking. This pins the encoding and proves
the derivation is behavior-identical to the old flat rule.

Run: python tests/test_overlap_trust_axes.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, Overlap, PrecisionLevel, TokenType, Trust, UsageSource  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(additivity, *, qty=100, prec=PrecisionLevel.EXACT, parent=None):
    return TokenQuantity(TokenType.INPUT, qty, prec, UsageSource.PROVIDER_RESPONSE, additivity, subtotal_of=parent)


# --- the encoding: each additivity value projects onto the two axes ---
tc = q(Additivity.TOTAL_CONTRIBUTING)
check(tc.overlap == Overlap.INDEPENDENT and tc.trust == Trust.VERIFIED, "total_contributing == (independent, verified)")

sub = TokenQuantity(TokenType.CACHED_INPUT, 80, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.SUBTOTAL_OF, subtotal_of="input")
check(sub.overlap == Overlap.SUBTOTAL_OF and sub.trust == Trust.VERIFIED, "subtotal_of == (subtotal_of, verified)")

unv = q(Additivity.UNVERIFIED)
check(unv.overlap == Overlap.INDEPENDENT and unv.trust == Trust.UNVERIFIED, "unverified == (independent, unverified)")

# --- the two axes explain the TWO distinct reasons a count is excluded from the total ---
check(sub.quantity_in_total == 0 and sub.trust == Trust.VERIFIED, "subtotal excluded for OVERLAP, though it is trusted")
check(unv.quantity_in_total == 0 and unv.overlap == Overlap.INDEPENDENT, "unverified excluded for TRUST, though it is independent")
check(tc.quantity_in_total == 100, "only an (independent, verified, known) count is summed")

# --- behaviour preserved: included_in_total still matches the old flat rule exactly ---
for a in (Additivity.TOTAL_CONTRIBUTING, Additivity.SUBTOTAL_OF, Additivity.UNVERIFIED):
    parent = "input" if a == Additivity.SUBTOTAL_OF else None
    tt = TokenType.CACHED_INPUT if a == Additivity.SUBTOTAL_OF else TokenType.INPUT
    known = TokenQuantity(tt, 50, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, a, subtotal_of=parent)
    old_rule = a == Additivity.TOTAL_CONTRIBUTING and known.quantity is not None
    check(known.included_in_total == old_rule, f"included_in_total unchanged for additivity={a.value} (got {known.included_in_total})")

# --- an unknown (None) independent+verified count is still excluded (INV-6), via the axes ---
lost = TokenQuantity(TokenType.OUTPUT, None, PrecisionLevel.UNKNOWN, UsageSource.NONE, Additivity.TOTAL_CONTRIBUTING)
check(
    lost.overlap == Overlap.INDEPENDENT and lost.trust == Trust.VERIFIED and lost.quantity_in_total == 0,
    "a lost independent+verified count still contributes 0 (unknown quantity, INV-6)",
)

# --- additivity remains the single stored field; the axes are NOT serialized (INV-1/INV-2) ---
d = tc.to_dict()
check(d.get("additivity") == "total_contributing", "additivity is still the stored wire field (non-breaking)")
check("overlap" not in d and "trust" not in d, "the derived axes are NOT serialized (INV-2)")
check(TokenQuantity.from_dict(d).overlap == Overlap.INDEPENDENT, "round-trip through storage re-derives the axes")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
