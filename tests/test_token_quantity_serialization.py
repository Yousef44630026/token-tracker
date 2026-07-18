"""Extra — TokenQuantity serialization round-trip across every token type (INV-1 / INV-2).

Run: python tests/test_token_quantity_serialization.py

to_dict/from_dict round-trips all stored fields for every TokenType (incl. None quantity with
an unknown_reason and metadata), and the derived keys never appear in the serialized dict.
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
DERIVED = {"included_in_total", "quantity_in_total", "export_warning"}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# every token type round-trips
for tt in TokenType:
    q = TokenQuantity(
        tt,
        123,
        PrecisionLevel.EXACT,
        UsageSource.PROVIDER_RESPONSE,
        Additivity.TOTAL_CONTRIBUTING,
        token_role="primary",
        metadata={"k": "v"},
    )
    d = q.to_dict()
    check(DERIVED.isdisjoint(d.keys()), f"{tt.value}: no derived key serialized")
    check(d["token_type"] == tt.value, f"{tt.value}: token_type serialized as its string")
    check(TokenQuantity.from_dict(d) == q, f"{tt.value}: from_dict(to_dict(q)) == q")

# a None/unknown quantity with a reason round-trips
unknown = TokenQuantity(
    TokenType.OUTPUT,
    None,
    PrecisionLevel.UNKNOWN,
    UsageSource.NONE,
    Additivity.TOTAL_CONTRIBUTING,
    unknown_reason=UnknownReason.STREAM_TIMEOUT,
)
du = unknown.to_dict()
check(du["quantity"] is None and du["unknown_reason"] == "stream_timeout", "unknown quantity serializes None + reason")
check(TokenQuantity.from_dict(du) == unknown, "unknown quantity round-trips")

# aggregation_mode + subtotal_of survive
sub = TokenQuantity(
    TokenType.CACHED_INPUT,
    800,
    PrecisionLevel.EXACT,
    UsageSource.PROVIDER_RESPONSE,
    Additivity.SUBTOTAL_OF,
    subtotal_of="input",
    aggregation_mode=AggregationMode.SUM,
)
check(TokenQuantity.from_dict(sub.to_dict()) == sub, "subtotal_of + aggregation_mode round-trip")
check(sub.to_dict()["subtotal_of"] == "input", "subtotal_of serialized")

# enums compare equal to their wire strings (str-Enum)
check(TokenType.OUTPUT == "output" and Additivity.UNVERIFIED == "unverified", "str-Enum members equal their wire string")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
