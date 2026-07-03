"""Regression (P4) — aggregation_mode must not silently promise unimplemented behavior.

AggregationMode.MAX / LAST are reserved but NOT honored: the derivation (quantity_in_total)
only ever sums. A quantity that declares MAX today would be silently summed — a field promising
behavior the engine does not implement. Fail closed: refuse to construct / read a quantity whose
aggregation_mode the engine cannot honor, rather than miscompute quietly. SUM (the default and
the only implemented mode) is of course accepted.

Run: python tests/test_aggregation_mode_fail_closed.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, AggregationMode, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def _base(**overrides):
    kwargs = dict(
        token_type=TokenType.INPUT,
        quantity=100,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
    )
    kwargs.update(overrides)
    return kwargs


# --- SUM (default) is accepted ---
q = TokenQuantity(**_base())
check(q.aggregation_mode == AggregationMode.SUM, "default aggregation_mode is SUM and is accepted")

# --- SUM (explicit) is accepted ---
q2 = TokenQuantity(**_base(aggregation_mode=AggregationMode.SUM))
check(q2.aggregation_mode == AggregationMode.SUM, "explicit SUM is accepted")

# --- MAX / LAST are refused at construction (fail closed, not silently summed) ---
for mode in (AggregationMode.MAX, AggregationMode.LAST):
    try:
        TokenQuantity(**_base(aggregation_mode=mode))
        check(False, f"{mode.value}: expected ValueError, but construction succeeded (silent trap)")
    except ValueError:
        check(True, f"{mode.value}: refused at construction — the engine only honors SUM")

# --- from_dict is the storage backstop: reading a stored non-SUM mode also fails closed ---
good = TokenQuantity(**_base()).to_dict()
restored = TokenQuantity.from_dict(good)
check(restored.aggregation_mode == AggregationMode.SUM, "from_dict round-trips SUM cleanly")

bad = dict(good)
bad["aggregation_mode"] = "max"
try:
    TokenQuantity.from_dict(bad)
    check(False, "from_dict('max'): expected ValueError, but it was read as if honored")
except ValueError:
    check(True, "from_dict refuses a stored non-SUM mode rather than silently summing it")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
