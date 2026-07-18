"""Regression (S2) — a timed-out stream must not THROW AWAY exact input it already received.

Sibling to test_stream_interrupt_keeps_known_usage.py (S1). Providers split stream usage:
Anthropic sends EXACT input tokens in message_start and the output count only later. If the
stream then stalls and the caller gives up with ``timeout()``, the tracker used to emit ONLY
an output=None/UNKNOWN quantity — silently dropping the exact input tokens already received
(an undercount of real, billed input). ``interrupt()`` was hardened against exactly this in
S1; ``timeout()`` is the OTHER lost-stream terminal state and had the same hole.

The contract for timeout is unchanged for OUTPUT (INV-6: a lost output is None/UNKNOWN with
reason stream_timeout, a surfaced count and never a confident zero). What changes is that a
KNOWN exact input — unambiguous provider data — is kept, exactly as interrupt() keeps it.

Run: python tests/test_stream_timeout_keeps_known_input.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.models.enums import (
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

check = make_checker()


def ctx(rcid="rc-1"):
    return TraceContext(trace_id="t", span_id="s", request_correlation_id=rcid)


def get_q(event, token_type):
    matches = [q for q in event.quantities if q.token_type == token_type]
    return matches[0] if matches else None


# --- 1) exact input arrived (message_start) then the stream stalls to a timeout --------------
tr = StreamTracker.from_context(ctx(), provider="anthropic", api_surface="messages")
tr.observe_usage(input_tokens=1500)  # Anthropic message_start exact input
tr.feed("")  # no output text ever arrived
to = tr.timeout()

out = get_q(to, TokenType.OUTPUT)
inp = get_q(to, TokenType.INPUT)

# OUTPUT contract is unchanged: a lost output is a surfaced unknown, never a confident zero.
check(
    out is not None and out.quantity is None,
    "timeout: output quantity stays None (INV-6)",
)
check(out.precision_level == PrecisionLevel.UNKNOWN, "timeout: output precision UNKNOWN")
check(
    out.unknown_reason == UnknownReason.STREAM_TIMEOUT,
    "timeout: output reason stream_timeout",
)
check(
    out.token_type == TokenType.OUTPUT,
    "timeout: output token_type stays 'output' (INV-3)",
)

# NEW: the exact input already received is kept, not silently dropped.
check(
    inp is not None and inp.quantity == 1500,
    "timeout keeps the exact input already received (1500)",
)
check(
    inp is not None and inp.precision_level == PrecisionLevel.EXACT and inp.usage_source == UsageSource.PROVIDER_RESPONSE,
    "kept input is EXACT / provider-sourced (it IS provider data)",
)
check(
    to.event_contributing_tokens == 1500,
    "timeout: known exact input counts; unknown output contributes 0",
)
check(
    "stream_interrupted" in to.data_quality_flags,
    "timeout still flags stream_interrupted",
)

# --- 2) explicit input_tokens argument is honored (parity with interrupt) --------------------
tr2 = StreamTracker.from_context(ctx("rc-2"), provider="anthropic", api_surface="messages")
to2 = tr2.timeout(input_tokens=88)
inp2 = get_q(to2, TokenType.INPUT)
check(
    inp2 is not None and inp2.quantity == 88,
    "timeout honors an explicit input_tokens argument (88)",
)

# an explicit value overrides a smaller observed one, matching interrupt()'s precedence
tr2b = StreamTracker.from_context(ctx("rc-2b"), provider="anthropic", api_surface="messages")
tr2b.observe_usage(input_tokens=10)
check(
    get_q(tr2b.timeout(input_tokens=88), TokenType.INPUT).quantity == 88,
    "explicit input overrides the observed value",
)

# --- 3) backward compatible: no known input -> no fabricated input quantity -------------------
tr3 = StreamTracker.from_context(ctx("rc-3"), provider="openai", api_surface="chat_completions")
to3 = tr3.timeout()
check(
    get_q(to3, TokenType.INPUT) is None,
    "timeout() with no known input: no fabricated input quantity",
)
check(
    get_q(to3, TokenType.OUTPUT).quantity is None,
    "timeout() with no known input: output still None/unknown",
)
check(to3.event_contributing_tokens == 0, "timeout() with no known input: contributes 0")

sys.exit(check.report("RESULT test_stream_timeout_keeps_known_input"))
