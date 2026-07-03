"""Extra — the canonical derived-field functions (INV-2 / INV-4 / INV-6).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_derived_fields.py

derive/derived_fields delegates to the model @property, so the rule lives in one place.
Verifies every export_warning branch and the event-grain derivations.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.derived_fields import (  # noqa: E402
    event_contributing_tokens,
    event_total_mismatch,
    export_warning,
    included_in_total,
    quantity_in_total,
)
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UnknownReason, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(qty, add, prec=PrecisionLevel.EXACT, parent=None, reason=None, src=UsageSource.PROVIDER_RESPONSE):
    return TokenQuantity(TokenType.OUTPUT, qty, prec, src, add, subtotal_of=parent, unknown_reason=reason)


contributing = q(300, Additivity.TOTAL_CONTRIBUTING)
subtotal = q(250, Additivity.SUBTOTAL_OF, parent="output")
unverified = q(80, Additivity.UNVERIFIED)
unknown = q(None, Additivity.TOTAL_CONTRIBUTING, prec=PrecisionLevel.UNKNOWN, reason=UnknownReason.STREAM_TIMEOUT, src=UsageSource.NONE)

# --- included_in_total / quantity_in_total ---
check(included_in_total(contributing) is True and quantity_in_total(contributing) == 300, "contributing: included, qit==300")
check(included_in_total(subtotal) is False and quantity_in_total(subtotal) == 0, "subtotal: excluded, qit==0")
check(included_in_total(unverified) is False and quantity_in_total(unverified) == 0, "unverified: excluded, qit==0")
check(included_in_total(unknown) is False and quantity_in_total(unknown) == 0, "unknown: excluded, qit==0")

# --- export_warning branches + precedence ---
check(export_warning(contributing) is None, "contributing: no warning")
check(export_warning(subtotal) == "subtotal_excluded_from_total", "subtotal warning")
check(export_warning(unverified) == "unverified_additivity_excluded_from_total", "unverified warning")
check(export_warning(unknown) == "unknown_quantity_excluded_from_total", "unknown warning")

# --- delegation: free function == model property ---
for qq in (contributing, subtotal, unverified, unknown):
    check(quantity_in_total(qq) == qq.quantity_in_total and export_warning(qq) == qq.export_warning, "free fn delegates to the property")

# --- event grain ---
live = TokenEvent(
    event_id="e1",
    request_correlation_id="r1",
    trace_id="t",
    span_id="s",
    quantities=[contributing, subtotal, unverified, unknown],
    provider_total_tokens=300,
)
check(event_contributing_tokens(live) == 300, "event contributing == 300 (only the contributing quantity)")
check(event_total_mismatch(live) == 0, "no mismatch (300 == 300)")

superseded = TokenEvent(
    event_id="e2",
    request_correlation_id="r2",
    trace_id="t",
    span_id="s",
    quantities=[contributing],
    superseded=True,
    superseded_by="e-final",
)
check(event_contributing_tokens(superseded) == 0, "superseded event contributes 0")

failed = TokenEvent(
    event_id="e-failed",
    request_correlation_id="r-failed",
    trace_id="t",
    span_id="s",
    quantities=[contributing],
    observation={"status": "failed", "authoritative": False},
)
check(
    event_contributing_tokens(failed) == 0,
    "explicitly non-authoritative observation contributes 0",
)

no_total = TokenEvent(event_id="e3", request_correlation_id="r3", trace_id="t", span_id="s", quantities=[contributing])
check(event_total_mismatch(no_total) is None, "no provider total -> mismatch None")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
