"""Regression (S3) — a FINAL-usage stream event must not THROW AWAY input already received.

Third sibling of test_stream_interrupt_keeps_known_usage.py (S1, interrupt) and
test_stream_timeout_keeps_known_input.py (S2, timeout). S1 and S2 hardened the two LOST
terminal states against dropping usage the tracker had already been told. ``complete()`` —
and therefore ``resolve_with_final_usage()``, which delegates to it — is the THIRD terminal
state and had the same hole.

Providers split stream usage. Anthropic sends the EXACT input tokens once, in message_start,
and the output count later in message_delta; the final usage frame carries OUTPUT ONLY. A
caller that hands the tracker that final frame verbatim —
``resolve_with_final_usage(output_tokens=120)`` — got an event with no input quantity at all,
even though ``observe_usage(input_tokens=1500)`` had already told the tracker the exact input.

That loss is SILENT, and supersession is what hides it: the interrupt partial DID carry the
exact input, but it is (correctly) superseded whole by the final so the input can never be
double counted (INV-5). So the 1500 real, billed input tokens live only on an event that now
contributes 0, and the pair sums to 120 instead of 1620 — with no flag, no mismatch, no crash.
Fixing this on the supersession side would be the wrong fix (keeping the partial alive risks
double counting the input whenever the final DOES restate it); the tracker must instead carry
the usage it already knows into the final, exactly as interrupt() and timeout() do.

Input tokens do not grow during a stream — message_start's input_tokens IS the request's final
input — so a recovered input is EXACT/provider-final data, not a floor. (The reverse is NOT
true for OUTPUT: a cumulative mid-stream output count is only ever a floor for an estimate and
must never be promoted into a final, which is why complete() still requires output_tokens.)

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_stream_final_keeps_known_input.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

check = make_checker()


def ctx(rcid="rc-1"):
    return TraceContext(trace_id="t", span_id="s", request_correlation_id=rcid)


def get_q(event, token_type):
    matches = [q for q in event.quantities if q.token_type == token_type]
    return matches[0] if matches else None


def anthropic_tracker(rcid):
    return StreamTracker.from_context(ctx(rcid), provider="anthropic", api_surface="messages")


# --- 1) THE SILENT LOSS: interrupt holds the exact input, the late final drops it -------------
tr = anthropic_tracker("rc-late-final")
tr.observe_usage(input_tokens=1500)  # message_start: exact input, provider-reported
tr.observe_usage(output_tokens=25)  # message_delta: cumulative output floor
tr.feed("some partial text")
partial = tr.interrupt()
check(
    get_q(partial, TokenType.INPUT).quantity == 1500,
    "precondition: the interrupt partial carries the exact input (1500)",
)

# The real final usage arrives late, carrying OUTPUT only (Anthropic's message_delta shape).
final = tr.resolve_with_final_usage(output_tokens=120)

check(partial.superseded is True, "the partial is still superseded by the final (INV-5)")
check(partial.event_contributing_tokens == 0, "the superseded partial contributes 0 (no double count)")

final_input = get_q(final, TokenType.INPUT)
check(
    final_input is not None and final_input.quantity == 1500,
    "the final carries forward the exact input the tracker already knew (1500)",
)
check(
    final_input is not None
    and final_input.precision_level == PrecisionLevel.EXACT
    and final_input.usage_source == UsageSource.PROVIDER_STREAM_FINAL,
    "the carried-forward input is EXACT / provider-final (input does not grow mid-stream)",
)
pair_total = partial.event_contributing_tokens + final.event_contributing_tokens
check(
    pair_total == 1620,
    f"the supersession pair sums to the FULL final usage: 1500 input + 120 output (got {pair_total})",
)

# --- 2) complete() has the same hole: a clean final frame with output only --------------------
tr2 = anthropic_tracker("rc-complete")
tr2.observe_usage(input_tokens=800)
done = tr2.complete(output_tokens=60)
check(
    get_q(done, TokenType.INPUT) is not None and get_q(done, TokenType.INPUT).quantity == 800,
    "complete() keeps the exact input already received (800)",
)
check(done.event_contributing_tokens == 860, "complete(): contributing = known input + final output")

# --- 3) an explicit input_tokens argument still wins (parity with interrupt/timeout) ----------
tr3 = anthropic_tracker("rc-explicit")
tr3.observe_usage(input_tokens=10)
check(
    get_q(tr3.complete(output_tokens=5, input_tokens=88), TokenType.INPUT).quantity == 88,
    "an explicit input_tokens overrides the observed value",
)

# --- 4) backward compatible: nothing known -> no fabricated input quantity --------------------
tr4 = StreamTracker.from_context(ctx("rc-none"), provider="openai", api_surface="chat_completions")
none_done = tr4.complete(output_tokens=7)
check(get_q(none_done, TokenType.INPUT) is None, "complete() with no known input: no fabricated input quantity")
check(none_done.event_contributing_tokens == 7, "complete() with no known input: contributes the output only")

# --- 5) a cumulative mid-stream OUTPUT count is never promoted into a final -------------------
# (complete() requires output_tokens; the observed output floor must not silently become EXACT)
tr5 = anthropic_tracker("rc-floor")
tr5.observe_usage(input_tokens=100, output_tokens=40)
final5 = tr5.complete(output_tokens=120)
out5 = get_q(final5, TokenType.OUTPUT)
check(
    out5.quantity == 120 and out5.precision_level == PrecisionLevel.EXACT,
    "the final output is the provider's real final count (120), not the observed floor (40)",
)

# --- 6) end-to-end through the reconciler, not just the tracker's own shortcut ----------------
tr6 = anthropic_tracker("rc-reconcile")
tr6.observe_usage(input_tokens=300)
tr6.feed("abc")
p6 = tr6.interrupt()
tr6b = anthropic_tracker("rc-reconcile")
tr6b.observe_usage(input_tokens=300)
f6 = tr6b.complete(output_tokens=90)
reconcile_supersession([p6, f6])
check(p6.superseded and not f6.superseded, "reconciler pairs the partial with its final by request_correlation_id")
check(
    p6.event_contributing_tokens + f6.event_contributing_tokens == 390,
    "reconciled group total == the full final usage (300 input + 90 output)",
)

sys.exit(check.report("RESULT test_stream_final_keeps_known_input"))
