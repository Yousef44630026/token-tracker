"""Extra — supersession edge cases (INV-5).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_supersession_edge_cases.py

Covers: no final usage (partial left intact), idempotency, multiple partials -> all
superseded, and the key INV-5 property: within ONE span holding two retries (two correlation
ids), each partial pairs with the final of the SAME request_correlation_id, never across.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def partial(eid, rcid, span="s-1", qty=40):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=rcid,
        trace_id="t-1",
        span_id=span,
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT, qty, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER, Additivity.TOTAL_CONTRIBUTING
            )
        ],
        observation={"authoritative": True},
    )


def final(eid, rcid, span="s-1", qty=200):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=rcid,
        trace_id="t-1",
        span_id=span,
        quantities=[
            TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=qty,
        observation={"authoritative": True},
    )


# --- no final in the group: the partial is left intact (supersession is never invented) ---
lone = partial("p-lone", "rc-lone")
reconcile_supersession([lone])
check(lone.superseded is False, "a lone partial (no final) stays not superseded")
check("superseded" not in lone.data_quality_flags, "no spurious 'superseded' flag")

# --- multiple partials + one final, same rcid -> all partials superseded ---
p1, p2, f1 = partial("p1", "rc-multi"), partial("p2", "rc-multi"), final("f1", "rc-multi")
events = [p1, p2, f1]
reconcile_supersession(events)
check(p1.superseded and p2.superseded, "both partials superseded by the final")
check(p1.superseded_by == "f1" and p2.superseded_by == "f1", "both point at the final event_id")
check(f1.superseded is False, "the final is not superseded")
check(sum(e.event_contributing_tokens for e in events) == 200, "total is the final usage only")

# --- idempotency: a second pass changes nothing, no duplicate flag ---
reconcile_supersession(events)
check(p1.data_quality_flags.count("superseded") == 1, "idempotent: 'superseded' flag not duplicated")

# --- INV-5: one span, two retries (two rcids) -> pair within the same rcid, not across ---
pa, fa = partial("pa", "rc-A", span="shared"), final("fa", "rc-A", span="shared", qty=210)
pb, fb = partial("pb", "rc-B", span="shared"), final("fb", "rc-B", span="shared", qty=305)
reconcile_supersession([pa, fa, pb, fb])
check(pa.superseded_by == "fa", "partial A paired with final A (same rcid)")
check(pb.superseded_by == "fb", "partial B paired with final B (same rcid)")
check(pa.superseded_by != "fb" and pb.superseded_by != "fa", "no cross-rcid pairing within the shared span")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
