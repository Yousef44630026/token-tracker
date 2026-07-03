"""Phase 2 / step 1 — TokenQuantity stored-vs-derived (INV-1 / INV-2 / INV-4).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_token_quantity.py

A TokenQuantity STORES source-of-truth only; included_in_total / quantity_in_total /
export_warning are DERIVED (@property) and must be absent from to_dict(). Verifies the
exact INV-2 derivation, including every export_warning branch and its precedence.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import (  # noqa: E402
    Additivity,
    AggregationMode,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0
_DERIVED_KEYS = {"included_in_total", "quantity_in_total", "export_warning"}


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def q(**kw) -> TokenQuantity:
    base = dict(
        token_type=TokenType.OUTPUT,
        quantity=100,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
        aggregation_mode=AggregationMode.SUM,
    )
    base.update(kw)
    return TokenQuantity(**base)


def main() -> int:
    # total_contributing with a known quantity -> counts
    tc = q()
    check(tc.included_in_total is True, "total_contributing + quantity -> included_in_total")
    check(tc.quantity_in_total == 100, "quantity_in_total == quantity when included")
    check(tc.export_warning is None, "clean contributing quantity has no export_warning")

    # subtotal_of -> contributes 0, warned
    sub = q(token_type=TokenType.CACHED_INPUT, additivity=Additivity.SUBTOTAL_OF, subtotal_of="input")
    check(sub.included_in_total is False, "subtotal_of -> not included_in_total")
    check(sub.quantity_in_total == 0, "subtotal_of contributes 0")
    check(
        sub.export_warning == "subtotal_excluded_from_total",
        "subtotal_of -> export_warning subtotal_excluded_from_total",
    )

    # unverified -> contributes 0, warned
    unv = q(additivity=Additivity.UNVERIFIED)
    check(unv.quantity_in_total == 0, "unverified contributes 0")
    check(
        unv.export_warning == "unverified_additivity_excluded_from_total",
        "unverified -> export_warning unverified_additivity_excluded_from_total",
    )

    # unknown quantity -> None, contributes 0, warned (INV-6)
    unk = q(
        quantity=None,
        precision_level=PrecisionLevel.UNKNOWN,
        usage_source=UsageSource.NONE,
        unknown_reason=UnknownReason.STREAM_TIMEOUT,
    )
    check(unk.included_in_total is False, "unknown quantity (None) -> not included")
    check(unk.quantity_in_total == 0, "unknown quantity contributes 0, not summed as confident zero")
    check(
        unk.export_warning == "unknown_quantity_excluded_from_total",
        "unknown -> export_warning unknown_quantity_excluded_from_total",
    )

    # precedence: subtotal_of takes precedence over the unknown branch
    sub_unknown = q(
        quantity=None,
        precision_level=PrecisionLevel.UNKNOWN,
        additivity=Additivity.SUBTOTAL_OF,
        subtotal_of="input",
    )
    check(
        sub_unknown.export_warning == "subtotal_excluded_from_total",
        "export_warning precedence: subtotal_of before unknown",
    )

    # serialization excludes derived fields, round-trips stored fields
    d = sub.to_dict()
    check(_DERIVED_KEYS.isdisjoint(d.keys()), "to_dict() excludes all derived keys")
    check(d["token_type"] == "cached_input", "to_dict serializes enums to their wire string")
    back = TokenQuantity.from_dict(d)
    check(back == sub, "to_dict -> from_dict round-trips stored fields exactly")
    check(back.quantity_in_total == 0, "read-back re-derives quantity_in_total")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
