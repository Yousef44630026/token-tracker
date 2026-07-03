"""DEEP logic test — property-based fuzzing of the core counting algebra + adversarial
supersession edge cases discovered by reading normalization/supersession.py closely.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_core_logic_deep.py

Part 1 (fuzz): thousands of randomly-constructed, but validation-legal, TokenEvents/Traces
are checked against the EXACT algebra the model promises:

    quantity_in_total          = quantity if (additivity == TOTAL_CONTRIBUTING
                                               and quantity is not None) else 0
    event_contributing_tokens  = 0 if (superseded or not authoritative)
                                  else sum(quantity_in_total over all quantities)
    trace rollup total         = sum(event_contributing_tokens over all events)

Hand-picked examples can miss combinations; this explores thousands of random combinations
of additivity / precision / None-ness / superseded / authoritative and asserts the exact
formula holds every time. The seed is fixed for reproducibility.

Part 2 (adversarial supersession): reconcile_supersession() picks the FIRST event in list
order satisfying `_is_final_usage` as "the" final and marks every OTHER partial-estimate
event in the same request_correlation_id group as superseded. This deliberately probes the
sharp edges that description implies: two final-qualifying events in one group, order
sensitivity, mixed-source events, many partials, repeated/interleaved reconciliation, and a
final event that itself carries subtotal quantities.

Part 3 (pathological values): large-N quantity lists, all-excluded events, a genuine known
zero vs. an unknown, and very large integers — checked for exact (never float) arithmetic.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import (  # noqa: E402
    Additivity,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402

_failures = 0
_checks = 0


def check(cond, msg):
    global _failures, _checks
    _checks += 1
    if not cond:
        _failures += 1
        print(f"[FAIL] {msg}")


_uid = 0


def uid(prefix="e"):
    global _uid
    _uid += 1
    return f"{prefix}-{_uid}"


# =====================================================================================
# PART 1 — property-based fuzz: the exact contribution algebra, thousands of times
# =====================================================================================

rng = random.Random(1234567)  # fixed seed: reproducible failures
ADDITIVITIES = [Additivity.TOTAL_CONTRIBUTING, Additivity.SUBTOTAL_OF, Additivity.UNVERIFIED]
QUANTITY_POOL = [0, 1, 2, 5, 100, 999_999, 10**12, 10**15]


def random_quantity() -> TokenQuantity:
    unknown = rng.random() < 0.15
    if unknown:
        quantity = None
        precision = PrecisionLevel.UNKNOWN
        usage_source = UsageSource.NONE
        unknown_reason = rng.choice(list(UnknownReason))
    else:
        quantity = rng.choice(QUANTITY_POOL)
        precision = rng.choice([PrecisionLevel.EXACT, PrecisionLevel.ESTIMATE])
        usage_source = rng.choice([UsageSource.PROVIDER_RESPONSE, UsageSource.PARTIAL_STREAM_TOKENIZER])
        unknown_reason = None
    additivity = rng.choice(ADDITIVITIES)
    subtotal_of = "input" if additivity == Additivity.SUBTOTAL_OF else None
    return TokenQuantity(
        token_type=rng.choice([TokenType.INPUT, TokenType.OUTPUT, TokenType.CACHED_INPUT]),
        quantity=quantity,
        precision_level=precision,
        usage_source=usage_source,
        additivity=additivity,
        subtotal_of=subtotal_of,
        unknown_reason=unknown_reason,
    )


def expected_contribution(quantities, superseded, authoritative) -> int:
    if superseded or not authoritative:
        return 0
    return sum(q.quantity for q in quantities if q.additivity == Additivity.TOTAL_CONTRIBUTING and q.quantity is not None)


def random_event() -> tuple[TokenEvent, bool, bool]:
    n = rng.randint(0, 12)
    quantities = [random_quantity() for _ in range(n)]
    superseded = rng.random() < 0.2
    authoritative = True if rng.random() < 0.85 else False
    kwargs = {}
    if superseded:
        kwargs["superseded_by"] = uid("final")
    if not authoritative:
        kwargs["observation"] = {"authoritative": False, "status": "failed"}
    event = TokenEvent(
        event_id=uid(),
        request_correlation_id=uid("r"),
        trace_id="t-fuzz",
        span_id="s-fuzz",
        quantities=quantities,
        superseded=superseded,
        **kwargs,
    )
    return event, superseded, authoritative


N_FUZZ = 3000
for _ in range(N_FUZZ):
    event, superseded, authoritative = random_event()
    expected = expected_contribution(event.quantities, superseded, authoritative)
    check(
        event.event_contributing_tokens == expected,
        f"fuzz event algebra: got {event.event_contributing_tokens}, expected {expected} "
        f"(n_q={len(event.quantities)}, superseded={superseded}, authoritative={authoritative})",
    )
    check(
        isinstance(event.event_contributing_tokens, int) and not isinstance(event.event_contributing_tokens, bool),
        "fuzz event algebra: result is a plain int (never float/bool)",
    )
    # per-quantity check, same run
    for q in event.quantities:
        exp_q = q.quantity if (q.additivity == Additivity.TOTAL_CONTRIBUTING and q.quantity is not None) else 0
        check(q.quantity_in_total == exp_q, f"fuzz quantity algebra: quantity_in_total mismatch ({q.quantity_in_total} != {exp_q})")

print(f"[INFO] Part 1: {N_FUZZ} random events fuzzed against the exact contribution formula.")

# =====================================================================================
# PART 1b — property-based fuzz: trace-level rollup, many random events per trace
# =====================================================================================

N_TRACES = 300
for trace_i in range(N_TRACES):
    trace = Trace(trace_id=f"t-fuzz-{trace_i}")
    expected_total = 0
    n_events = rng.randint(0, 20)
    for _ in range(n_events):
        n_q = rng.randint(0, 6)
        quantities = [random_quantity() for _ in range(n_q)]
        superseded = rng.random() < 0.2
        authoritative = rng.random() >= 0.1
        kwargs = {}
        if superseded:
            kwargs["superseded_by"] = uid("final")
        if not authoritative:
            kwargs["observation"] = {"authoritative": False}
        ev = TokenEvent(
            event_id=uid(),
            request_correlation_id=uid("r"),
            trace_id=trace.trace_id,
            span_id="s",
            quantities=quantities,
            superseded=superseded,
            **kwargs,
        )
        trace.add_event(ev)
        expected_total += expected_contribution(quantities, superseded, authoritative)
    got = observed_total_contributing_tokens(trace)
    check(got == expected_total, f"fuzz trace rollup #{trace_i}: got {got}, expected {expected_total} ({n_events} events)")

print(f"[INFO] Part 1b: {N_TRACES} random traces fuzzed against the exact rollup sum.")

# =====================================================================================
# PART 2 — adversarial supersession edge cases (hand-crafted, targeting real ambiguities)
# =====================================================================================


def out_q(qty, source):
    return TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, source, Additivity.TOTAL_CONTRIBUTING)


def partial(eid, rcid, qty=10):
    return TokenEvent(
        event_id=eid, request_correlation_id=rcid, trace_id="t", span_id="s", quantities=[out_q(qty, UsageSource.PARTIAL_STREAM_TOKENIZER)]
    )


def final(eid, rcid, qty=100, total=None, timestamp=None):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=rcid,
        trace_id="t",
        span_id="s",
        quantities=[out_q(qty, UsageSource.PROVIDER_RESPONSE)],
        provider_total_tokens=total if total is not None else qty,
        timestamp=timestamp,
    )


# --- 2.1: TWO final-qualifying events in the same rcid group (duplicate delivery) ---
# One request_correlation_id == one logical attempt (a retry gets its OWN new rcid), so two
# final-usage events sharing an rcid represent a DUPLICATE measurement of the same attempt
# (e.g. an at-least-once completion callback firing twice) — only one must contribute.
# Without timestamps, the tie-break is deterministic: the first in input order wins, and the
# OTHER final is superseded exactly like a partial would be.
p = partial("p1", "rc-dup", qty=5)
f1 = final("f1", "rc-dup", qty=100)
f2 = final("f2", "rc-dup", qty=200)
group = [p, f1, f2]
reconcile_supersession(group)
check(p.superseded is True and p.superseded_by == "f1", "2.1: the partial is superseded by the FIRST final in list order")
check(f1.superseded is False, "2.1: the winning (first) final is NOT superseded")
check(f2.superseded is True and f2.superseded_by == "f1", "2.1: the DUPLICATE final (f2) is now also superseded, treated like a partial")
total = sum(e.event_contributing_tokens for e in group)
check(total == 100, f"2.1 FIXED: only the ONE authoritative final contributes (100, not 100+200=300) — no more double count (got {total})")

# --- 2.2: with timestamps present, the LATEST-timestamped final wins, regardless of list order ---
p_b = partial("p1b", "rc-dup-b", qty=5)
f1_b = final("f1b", "rc-dup-b", qty=100, timestamp="2026-01-01T10:00:05Z")  # later timestamp, listed SECOND
f2_b = final("f2b", "rc-dup-b", qty=200, timestamp="2026-01-01T10:00:00Z")  # earlier timestamp, listed FIRST
reconcile_supersession([p_b, f2_b, f1_b])
check(
    f1_b.superseded is False and f2_b.superseded is True,
    "2.2: the LATEST-timestamped final (f1b) wins even though f2b appeared first in the list",
)
check(
    p_b.superseded_by == "f1b" and f2_b.superseded_by == "f1b",
    "2.2: both the partial and the earlier-timestamped duplicate final point at the true winner",
)
check(
    sum(e.event_contributing_tokens for e in [p_b, f1_b, f2_b]) == 100,
    "2.2: total is exactly the winning final's usage (100), timestamp-based tie-break honored",
)

# --- 2.2b: idempotency still holds with the new duplicate-final handling ---
reconcile_supersession([p_b, f2_b, f1_b])
check(f2_b.data_quality_flags.count("superseded") == 1, "2.2b: re-running does not duplicate the 'superseded' flag on the demoted final")

# --- 2.3: a mixed-source event (not ALL quantities partial) is NEVER a supersession candidate ---
mixed = TokenEvent(
    event_id="mixed",
    request_correlation_id="rc-mixed",
    trace_id="t",
    span_id="s",
    quantities=[out_q(5, UsageSource.PARTIAL_STREAM_TOKENIZER), out_q(3, UsageSource.PROVIDER_RESPONSE)],
)
f_mixed = final("f-mixed", "rc-mixed", qty=50)
reconcile_supersession([mixed, f_mixed])
check(
    mixed.superseded is False,
    "2.3: an event with mixed usage_source quantities is never treated as a partial estimate (all-or-nothing check)",
)
check(
    mixed.event_contributing_tokens == 3 + 5, "2.3: the un-superseded mixed event still contributes its own total_contributing quantities"
)

# --- 2.4: many partials (5) all superseded by one final ---
partials = [partial(f"multi-p{i}", "rc-multi", qty=i + 1) for i in range(5)]
f_multi = final("f-multi", "rc-multi", qty=999)
group = [*partials, f_multi]
reconcile_supersession(group)
check(all(p.superseded and p.superseded_by == "f-multi" for p in partials), "2.4: all 5 partials superseded by the single final")
check(sum(e.event_contributing_tokens for e in group) == 999, "2.4: total is exactly the final's usage, no partial leaks in")

# --- 2.5: idempotent + interleaved — reconcile, add more events, reconcile again (x3) ---
p1 = partial("interleave-p1", "rc-il")
events = [p1]
reconcile_supersession(events)
check(p1.superseded is False, "2.5 pass 1: lone partial (no final yet) stays un-superseded")
f_il = final("interleave-f", "rc-il", qty=42)
events.append(f_il)
reconcile_supersession(events)
check(
    p1.superseded is True and p1.data_quality_flags.count("superseded") == 1, "2.5 pass 2: final arrives, partial superseded exactly once"
)
p2 = partial("interleave-p2", "rc-il", qty=7)  # a second, later partial for the SAME rcid
events.append(p2)
reconcile_supersession(events)
check(
    p2.superseded is True and p1.data_quality_flags.count("superseded") == 1,
    "2.5 pass 3: a newly-added late partial also gets superseded; the first partial's flag is NOT duplicated by re-running",
)
check(sum(e.event_contributing_tokens for e in events) == 42, "2.5: final total unaffected by however many reconciliation passes ran")

# --- 2.6: a final event with its OWN subtotal quantities, alongside an unrelated superseded partial ---
final_with_subtotal = TokenEvent(
    event_id="f-sub",
    request_correlation_id="rc-sub",
    trace_id="t",
    span_id="s",
    quantities=[
        out_q(100, UsageSource.PROVIDER_RESPONSE),
        TokenQuantity(
            TokenType.CACHED_INPUT, 80, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.SUBTOTAL_OF, subtotal_of="input"
        ),
    ],
    provider_total_tokens=100,
)
p_sub = partial("p-sub", "rc-sub", qty=15)
reconcile_supersession([p_sub, final_with_subtotal])
check(p_sub.superseded is True, "2.6: partial superseded as usual")
check(
    final_with_subtotal.event_contributing_tokens == 100,
    "2.6: final's own subtotal (80, cached) correctly excluded — supersession machinery doesn't disturb additivity accounting",
)

# =====================================================================================
# PART 3 — pathological / extreme values
# =====================================================================================

# --- 3.1: large-N (50) quantities, mixed additivity, exact sum ---
many_quantities = []
expected_many = 0
for i in range(50):
    additivity = [Additivity.TOTAL_CONTRIBUTING, Additivity.SUBTOTAL_OF, Additivity.UNVERIFIED][i % 3]
    subtotal_of = "input" if additivity == Additivity.SUBTOTAL_OF else None
    qty = i * 7
    many_quantities.append(
        TokenQuantity(TokenType.OUTPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, additivity, subtotal_of=subtotal_of)
    )
    if additivity == Additivity.TOTAL_CONTRIBUTING:
        expected_many += qty
ev_many = TokenEvent(event_id=uid(), request_correlation_id=uid(), trace_id="t", span_id="s", quantities=many_quantities)
check(
    ev_many.event_contributing_tokens == expected_many,
    f"3.1: 50-quantity event sums exactly (got {ev_many.event_contributing_tokens}, expected {expected_many})",
)

# --- 3.2: an event with ZERO quantities (empty list) contributes 0, no crash ---
ev_empty = TokenEvent(event_id=uid(), request_correlation_id=uid(), trace_id="t", span_id="s", quantities=[])
check(ev_empty.event_contributing_tokens == 0, "3.2: empty-quantities event contributes 0 (no crash on empty sum)")

# --- 3.3: an event where NO quantity is total_contributing (all subtotal/unverified) ---
ev_no_contrib = TokenEvent(
    event_id=uid(),
    request_correlation_id=uid(),
    trace_id="t",
    span_id="s",
    quantities=[
        TokenQuantity(
            TokenType.CACHED_INPUT, 500, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.SUBTOTAL_OF, subtotal_of="input"
        ),
        TokenQuantity(TokenType.OUTPUT, 300, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.UNVERIFIED),
    ],
)
check(ev_no_contrib.event_contributing_tokens == 0, "3.3: all-excluded event (no total_contributing entries) -> 0")

# --- 3.4: a genuine KNOWN zero (quantity=0, total_contributing) vs an UNKNOWN (None) ---
ev_known_zero = TokenEvent(
    event_id=uid(),
    request_correlation_id=uid(),
    trace_id="t",
    span_id="s",
    quantities=[TokenQuantity(TokenType.OUTPUT, 0, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)],
)
check(
    ev_known_zero.event_contributing_tokens == 0 and ev_known_zero.quantities[0].included_in_total is True,
    "3.4: a real zero is INCLUDED in the total (included_in_total=True), unlike an unknown",
)
ev_unknown = TokenEvent(
    event_id=uid(),
    request_correlation_id=uid(),
    trace_id="t",
    span_id="s",
    quantities=[
        TokenQuantity(
            TokenType.OUTPUT,
            None,
            PrecisionLevel.UNKNOWN,
            UsageSource.NONE,
            Additivity.TOTAL_CONTRIBUTING,
            unknown_reason=UnknownReason.STREAM_TIMEOUT,
        )
    ],
)
check(
    ev_unknown.event_contributing_tokens == 0 and ev_unknown.quantities[0].included_in_total is False,
    "3.4: an unknown quantity is EXCLUDED (included_in_total=False) — same numeric total as a real zero, different flag (INV-6)",
)

# --- 3.5: very large integers stay exact ints (no float creep anywhere in the chain) ---
huge = 10**15 + 7
ev_huge = TokenEvent(
    event_id=uid(),
    request_correlation_id=uid(),
    trace_id="t",
    span_id="s",
    quantities=[TokenQuantity(TokenType.INPUT, huge, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)],
    provider_total_tokens=huge,
)
check(ev_huge.event_contributing_tokens == huge, "3.5: a 10**15-scale quantity sums exactly")
check(type(ev_huge.event_contributing_tokens) is int, "3.5: result type is exactly int, not float")
check(ev_huge.event_total_mismatch == 0, "3.5: mismatch check holds exactly at this scale too (no float rounding)")

# --- 3.6: negative quantities are rejected at construction (re-confirm the hard guard) ---
rejected = False
try:
    TokenQuantity(TokenType.OUTPUT, -1, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
except ValueError:
    rejected = True
check(rejected, "3.6: negative quantity is rejected at construction (cannot even build a corrupting value)")

print(f"\n[INFO] total checks run: {_checks}")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
