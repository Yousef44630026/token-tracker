"""Extra — normalizer data-quality flags (single producer each).

Run: python tests/test_data_quality_flags.py

The normalizer produces exactly three flags from the quantities + provider total:
unverified_additivity, unknown_quantity_present, provider_total_mismatch. A clean event
raises none; the normalizer never emits flags owned by other producers.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UnknownReason, UsageSource  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def tc(tt, qty, src=UsageSource.PROVIDER_RESPONSE, prec=PrecisionLevel.EXACT, add=Additivity.TOTAL_CONTRIBUTING, parent=None, reason=None):
    return TokenQuantity(tt, qty, prec, src, add, subtotal_of=parent, unknown_reason=reason)


# --- clean event: no flags ---
clean = [tc(TokenType.INPUT, 100), tc(TokenType.OUTPUT, 50)]
check(normalizer_flags(clean, 150) == [], "clean event raises no flags")

# --- unverified additivity ---
unv = [tc(TokenType.INPUT, 100), tc(TokenType.CACHED_INPUT, 80, add=Additivity.UNVERIFIED)]
check("unverified_additivity" in normalizer_flags(unv, 100), "unverified quantity -> unverified_additivity")

# --- unknown quantity present (None / unknown precision) ---
unk = [tc(TokenType.OUTPUT, None, src=UsageSource.NONE, prec=PrecisionLevel.UNKNOWN, reason=UnknownReason.STREAM_TIMEOUT)]
check("unknown_quantity_present" in normalizer_flags(unk, None), "unknown quantity -> unknown_quantity_present")

# --- provider total mismatch ---
mism = [tc(TokenType.INPUT, 100), tc(TokenType.OUTPUT, 50)]
check("provider_total_mismatch" in normalizer_flags(mism, 999), "provider total != derived -> provider_total_mismatch")
check("provider_total_mismatch" not in normalizer_flags(mism, 150), "matching totals -> no mismatch flag")
check(normalizer_flags(mism, None) == [], "no provider total -> mismatch cannot be judged (no flag)")

# --- single-producer boundary: normalizer never emits other producers' flags ---
flags = normalizer_flags(unv + unk, 100)
for foreign in ("superseded", "stream_interrupted", "partial_stream_estimate", "raw_usage_missing", "propagation_lost"):
    check(foreign not in flags, f"normalizer does not emit '{foreign}' (owned elsewhere)")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
