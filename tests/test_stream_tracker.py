"""Phase 7 — streaming tracker lifecycle (INV-3 / INV-5 / INV-6).

Run: python tests/test_stream_tracker.py

A streamed call has four terminal states, and the tracker assigns the right precision and
flags for each — never a forbidden token_type (output stays "output" throughout, INV-3):
  - completed  -> output EXACT from the provider's final usage;
  - interrupted-> output ESTIMATE (partial tokenizer), flags partial_stream_estimate +
                  stream_interrupted;
  - final arrives after an interrupt -> the partial is superseded by request_correlation_id
                  (INV-5), so the contributing total is the FINAL usage only;
  - timeout    -> output quantity None / UNKNOWN with reason stream_timeout (INV-6: a lost
                  count is never a confident zero).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context import headers as context_headers  # noqa: E402
from tracker.context.propagation import continue_from_headers, new_trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UnknownReason, UsageSource  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def new_tracker(rcid="rcid-1"):
    return StreamTracker(request_correlation_id=rcid, trace_id="t-1", span_id="s-1", provider="openai")


# --- 1) clean completion -> EXACT ---
t = new_tracker()
t.feed("Hello ")
t.feed("world, this is the answer.")
done = t.complete(output_tokens=210, input_tokens=50, provider_total_tokens=260)
oq = next(q for q in done.quantities if q.token_type == TokenType.OUTPUT)
check(oq.token_type == TokenType.OUTPUT, "completed: token_type stays 'output' (INV-3)")
check(oq.precision_level == PrecisionLevel.EXACT, "completed: output is EXACT")
check(oq.usage_source == UsageSource.PROVIDER_STREAM_FINAL, "completed: source is provider_stream_final")
check(oq.quantity == 210, "completed: exact output quantity")
check(done.event_contributing_tokens == 260, "completed: contributes input+output")
check("partial_stream_estimate" not in done.data_quality_flags, "completed: no estimate flag")

# --- 2) interruption -> ESTIMATE partial ---
t = new_tracker("rcid-2")
t.feed("partial answer so far")
partial = t.interrupt()
pq = next(q for q in partial.quantities if q.token_type == TokenType.OUTPUT)
check(pq.token_type == TokenType.OUTPUT, "interrupted: token_type stays 'output' (INV-3)")
check(pq.precision_level == PrecisionLevel.ESTIMATE, "interrupted: output is ESTIMATE")
check(pq.usage_source == UsageSource.PARTIAL_STREAM_TOKENIZER, "interrupted: source is partial_stream_tokenizer")
check(pq.quantity is not None and pq.quantity > 0, "interrupted: a positive estimated quantity")
check(
    pq.metadata.get("estimator") in {"tokentap_cl100k_base", "tracker_char4_fallback"},
    "interrupted: estimator backend is persisted",
)
check(pq.metadata.get("text_characters") == len("partial answer so far"), "interrupted: estimate input size is auditable")
check("partial_stream_estimate" in partial.data_quality_flags, "interrupted: partial_stream_estimate flag")
check("stream_interrupted" in partial.data_quality_flags, "interrupted: stream_interrupted flag")

# --- 3) final arrives after the interrupt -> supersede the partial, count final only ---
final = t.resolve_with_final_usage(output_tokens=180, input_tokens=40, provider_total_tokens=220)
check(partial.superseded is True, "late final: partial is superseded")
check(partial.superseded_by == final.event_id, "late final: superseded_by == final.event_id")
check("superseded" in partial.data_quality_flags, "late final: 'superseded' flag on the partial")
total = partial.event_contributing_tokens + final.event_contributing_tokens
check(total == 220, f"late final: total is the final usage only (got {total})")
check(partial.request_correlation_id == final.request_correlation_id, "matched by request_correlation_id, not span_id")

# --- 4) timeout -> None / UNKNOWN, surfaced not zeroed ---
t = new_tracker("rcid-3")
t.feed("nothing will resolve")
to = t.timeout()
tq = next(q for q in to.quantities if q.token_type == TokenType.OUTPUT)
check(tq.token_type == TokenType.OUTPUT, "timeout: token_type stays 'output' (INV-3)")
check(tq.quantity is None, "timeout: quantity is None (not 0)")
check(tq.precision_level == PrecisionLevel.UNKNOWN, "timeout: precision UNKNOWN")
check(tq.unknown_reason == UnknownReason.STREAM_TIMEOUT, "timeout: reason stream_timeout")
check(to.event_contributing_tokens == 0, "timeout: contributes 0 (a known-unknown, INV-6)")
check(tq.export_warning == "unknown_quantity_excluded_from_total", "timeout: surfaced as unknown, not summed")

# --- 5) cross-service propagation loss follows the streamed event automatically ---
partial_headers = context_headers.inject(new_trace())
del partial_headers["X-TokenTracker-Span-Id"]
with continue_from_headers(partial_headers) as resumed:
    lost_stream = StreamTracker.from_context(resumed.context, provider="openai").complete(
        output_tokens=2,
        input_tokens=3,
        provider_total_tokens=5,
    )
check("propagation_lost" in lost_stream.data_quality_flags, "stream event automatically records lost inbound propagation")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
