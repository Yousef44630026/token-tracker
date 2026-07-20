"""Regression (L1) — correlation-id COLLISION must be visible, not a silent undercount.

INV-5 assumes one request_correlation_id == one logical attempt, so two final-usage events
sharing a correlation id are treated as a duplicate delivery of the SAME call and one is
superseded (contributes 0) to avoid double-counting. That is correct when they really are the
same call. But if the upstream invariant is ever violated — an id collision, or a caller
reusing a correlation id for a genuinely different call — superseding one silently DROPS a real
call's tokens (an undercount) with no signal.

The event already carries request_hash / response_hash. Supersession must use them to tell the
two cases apart:
  - same content hashes  -> a true duplicate delivery: supersede quietly (only 'superseded').
  - different hashes      -> a suspicious collision: still supersede (never overcount), but ALSO
                            raise a distinct 'correlation_id_collision' flag so the dropped
                            tokens are auditable instead of invisible.

Run: python tests/test_supersession_collision_regression.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import roll_up  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.supersession import (  # noqa: E402
    COLLISION_FLAG,
    SUPERSEDED_FLAG,
    UNVERIFIED_DUPLICATE_FLAG,
    reconcile_supersession,
)

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def final(eid, rcid, qty, *, request_hash=None, response_hash=None, ts=None):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=rcid,
        trace_id="t-1",
        span_id="s-1",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=qty,
        request_hash=request_hash,
        response_hash=response_hash,
        timestamp=ts,
        observation={"authoritative": True},
    )


# --- COLLISION: two finals, same rcid, DIFFERENT response hash = two different calls ---
a = final("call-a", "rc-shared", 200, request_hash="req-A", response_hash="resp-A", ts="2026-07-03T10:00:00")
b = final("call-b", "rc-shared", 500, request_hash="req-B", response_hash="resp-B", ts="2026-07-03T10:00:01")
events = [a, b]
reconcile_supersession(events)

kept = [e for e in events if not e.superseded]
dropped = [e for e in events if e.superseded]
check(len(kept) == 1 and len(dropped) == 1, "collision: exactly one final kept, one superseded (never overcount)")
check(dropped[0].event_contributing_tokens == 0, "collision: the superseded final contributes 0")
check(SUPERSEDED_FLAG in dropped[0].data_quality_flags, "collision: 'superseded' still raised")
check(
    COLLISION_FLAG in dropped[0].data_quality_flags,
    "collision: 'correlation_id_collision' raised so the dropped real tokens are VISIBLE, not silent",
)
check(COLLISION_FLAG not in kept[0].data_quality_flags, "collision: the kept final is not itself flagged as a collision")
collision_rollup = roll_up(Trace(trace_id="t-1", events=events))
check(
    (
        collision_rollup.headline_floor_tokens,
        collision_rollup.headline_estimate_tokens,
        collision_rollup.headline_ceiling_tokens,
    )
    == (500, 500, 700),
    "collision: headline keeps the conservative point and carries the dropped candidate in its ceiling",
)
check(
    collision_rollup.total_is_lower_bound is True,
    "collision: headline cannot claim an exact total after dropping a distinct measurement",
)
check(collision_rollup.headline_status == "bounded", "collision: uncertainty is finite and explicit")

# --- TRUE DUPLICATE: two finals, same rcid, SAME hashes = one call delivered twice ---
d1 = final("dup-1", "rc-dup", 300, request_hash="req-X", response_hash="resp-X", ts="2026-07-03T11:00:00")
d2 = final("dup-2", "rc-dup", 300, request_hash="req-X", response_hash="resp-X", ts="2026-07-03T11:00:01")
dup_events = [d1, d2]
reconcile_supersession(dup_events)
dup_dropped = [e for e in dup_events if e.superseded]
check(len(dup_dropped) == 1, "duplicate: exactly one superseded")
check(
    COLLISION_FLAG not in dup_dropped[0].data_quality_flags,
    "duplicate: NO collision flag when the content hashes match (a genuine at-least-once redelivery)",
)

# --- UNKNOWN hashes: cannot prove a duplicate -> widen uncertainty, never stay silent ---
u1 = final("u-1", "rc-unk", 100, ts="2026-07-03T12:00:00")
u2 = final("u-2", "rc-unk", 100, ts="2026-07-03T12:00:01")
unk_events = [u1, u2]
reconcile_supersession(unk_events)
unk_dropped = [e for e in unk_events if e.superseded]
check(len(unk_dropped) == 1, "unknown-hash: exactly one superseded")
check(
    COLLISION_FLAG not in unk_dropped[0].data_quality_flags,
    "unknown-hash: no proven-collision label when hashes are absent",
)
check(
    UNVERIFIED_DUPLICATE_FLAG in unk_dropped[0].data_quality_flags,
    "unknown-hash: absence of duplicate evidence is explicit",
)
unknown_rollup = roll_up(Trace(trace_id="t-1", events=unk_events))
check(
    (
        unknown_rollup.headline_floor_tokens,
        unknown_rollup.headline_estimate_tokens,
        unknown_rollup.headline_ceiling_tokens,
    )
    == (100, 100, 200),
    "unknown-hash: dropped final expands the possible ceiling",
)
check(unknown_rollup.total_is_lower_bound is True, "unknown-hash: total cannot claim exactness")

# --- a partial superseded by its final legitimately differs in content: NOT a collision ---
part = TokenEvent(
    event_id="p-1",
    request_correlation_id="rc-part",
    trace_id="t-1",
    span_id="s-1",
    quantities=[
        TokenQuantity(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER, Additivity.TOTAL_CONTRIBUTING)
    ],
    request_hash="req-P",
    response_hash="resp-partial",
    observation={"authoritative": True},
)
fin = final("f-part", "rc-part", 200, request_hash="req-P", response_hash="resp-final")
part_events = [part, fin]
reconcile_supersession(part_events)
check(part.superseded and not fin.superseded, "partial: superseded by its final as usual")
check(
    COLLISION_FLAG not in part.data_quality_flags,
    "partial: a partial estimate differing from its final is expected, NOT a collision",
)

# --- timestamp offsets: choose the latest instant, not lexicographically largest string ---
offset_early = final(
    "offset-early",
    "rc-offset",
    100,
    request_hash="req-offset",
    response_hash="resp-offset",
    ts="2026-01-01T10:00:00+02:00",
)
utc_late = final(
    "utc-late",
    "rc-offset",
    100,
    request_hash="req-offset",
    response_hash="resp-offset",
    ts="2026-01-01T09:00:00Z",
)
offset_events = [offset_early, utc_late]
reconcile_supersession(offset_events)
check(utc_late.superseded is False, "timestamp offsets: latest UTC instant is kept")
check(offset_early.superseded_by == "utc-late", "timestamp offsets: older offset timestamp is superseded")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
