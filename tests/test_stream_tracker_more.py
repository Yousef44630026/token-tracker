"""Extra — stream tracker, additional paths (Phase 7).

Run: python tests/test_stream_tracker_more.py

Text accumulation, output-only completion, a late final with no prior partial (no crash),
an interrupt on empty text (estimate 0), and provider/model passthrough onto the event.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def tracker(rcid="rc"):
    return StreamTracker(request_correlation_id=rcid, trace_id="t", span_id="s", provider="openai", model="gpt-4o")


def out_q(ev):
    return next(q for q in ev.quantities if q.token_type == TokenType.OUTPUT)


# --- text accumulation ---
t = tracker()
t.feed("Hello ")
t.feed("world")
t.feed("")  # empty delta ignored
check(t.accumulated_text == "Hello world", "feed accumulates text, ignores empty deltas")

# --- output-only clean completion (no input, no provider total) ---
done = tracker().complete(output_tokens=50)
check(len(done.quantities) == 1 and out_q(done).quantity == 50, "output-only completion has just the output")
check(done.event_contributing_tokens == 50, "output-only contributes 50")
check(done.provider == "openai" and done.model == "gpt-4o", "provider/model passthrough onto the event")

# --- late final with NO prior partial: returns the final, supersedes nothing, no crash ---
final = tracker().resolve_with_final_usage(output_tokens=180, input_tokens=40, provider_total_tokens=220)
check(final.superseded is False and final.event_contributing_tokens == 220, "late final w/o partial: not superseded, 220")

# --- interrupt on empty text -> estimate 0, still an ESTIMATE ---
ti = tracker()
partial = ti.interrupt()
check(out_q(partial).quantity == 0 and out_q(partial).precision_level == PrecisionLevel.ESTIMATE, "empty interrupt -> estimate 0")
check("partial_stream_estimate" in partial.data_quality_flags, "empty interrupt still flags partial_stream_estimate")

# --- timeout path ---
to = tracker().timeout()
check(out_q(to).quantity is None and out_q(to).precision_level == PrecisionLevel.UNKNOWN, "timeout -> None/unknown")
check(to.event_contributing_tokens == 0, "timeout contributes 0")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
