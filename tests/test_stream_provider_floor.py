"""Streaming robustness — an interrupted stream uses the provider's own mid-stream count.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_stream_provider_floor.py

Many providers (Anthropic message_delta, some Azure/OpenAI stream_options) emit CUMULATIVE
usage WHILE streaming. If the stream is then interrupted, the provider's own last count is a
near-exact FLOOR of the output — far better than a ~4-char/token tokenizer guess. The tracker
should ingest those mid-stream usage events (observe_usage) and use the latest automatically on
interrupt, labeled with a dedicated provenance (PROVIDER_STREAM_PARTIAL) so it is never
confused with a complete response — while staying an ESTIMATE (a floor of the final), still
superseded by the real final usage (INV-5).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.context.model import TraceContext  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

check = make_checker()


def tracker():
    return StreamTracker(
        request_correlation_id="corr-1",
        trace_id="tr-1",
        span_id="sp-1",
        provider="openai",
        api_surface="chat_completions",
    )


def output_q(event):
    return next((q for q in event.quantities if q.token_type == TokenType.OUTPUT), None)


def input_q(event):
    return next((q for q in event.quantities if q.token_type == TokenType.INPUT), None)


# --- 1. Auto-ingest: the provider's mid-stream count beats the tokenizer on interrupt --------
st = tracker()
st.observe_usage(output_tokens=40)  # provider said 40 output tokens so far
st.feed("short")  # tokenizer would estimate ~1-2 tokens
partial = st.interrupt()
out = output_q(partial)
check(out is not None and out.quantity == 40, "interrupt uses the provider's mid-stream output count (40), not the tokenizer guess")
check(
    out.usage_source == UsageSource.PROVIDER_STREAM_PARTIAL,
    "the provider-counted floor is labeled PROVIDER_STREAM_PARTIAL (honest provenance)",
)
check(out.precision_level == PrecisionLevel.ESTIMATE, "it stays an ESTIMATE — a floor of the final, not the final")
check("partial_stream_estimate" in partial.data_quality_flags and "stream_interrupted" in partial.data_quality_flags, "flags preserved")

# --- 2. Monotonic: cumulative counts grow, so the latest observation wins --------------------
st = tracker()
st.observe_usage(output_tokens=40)
st.observe_usage(output_tokens=55)
check(output_q(st.interrupt()).quantity == 55, "the latest cumulative provider count (55) is used")

# out-of-order / stale update must not lower the floor
st = tracker()
st.observe_usage(output_tokens=55)
st.observe_usage(output_tokens=40)  # a stale/duplicate event
check(output_q(st.interrupt()).quantity == 55, "a stale lower count never lowers the floor (monotonic)")

# --- 3. Mid-stream input is captured EXACT ---------------------------------------------------
st = tracker()
st.observe_usage(input_tokens=100, output_tokens=40)
partial = st.interrupt()
iq = input_q(partial)
check(iq is not None and iq.quantity == 100 and iq.precision_level == PrecisionLevel.EXACT, "mid-stream input is recorded EXACT (100)")

# --- 4. Backward compatible: no observation -> tokenizer estimate as before ------------------
st = tracker()
st.feed("hello world " * 8)  # ~96 chars -> ~24 token estimate
partial = st.interrupt()
out = output_q(partial)
check(
    out.quantity > 0 and out.usage_source == UsageSource.PARTIAL_STREAM_TOKENIZER,
    "with no provider count, interrupt falls back to the tokenizer estimate",
)

# --- 5. Explicit argument still overrides the observed value (backward compatible) ------------
st = tracker()
st.observe_usage(output_tokens=40)
check(output_q(st.interrupt(output_tokens_seen=99)).quantity == 99, "an explicit output_tokens_seen still overrides the observed value")

# --- 6. The tokenizer wins if it is somehow higher than a stale provider count ---------------
st = tracker()
st.observe_usage(output_tokens=2)
st.feed("word " * 40)  # tokenizer estimate well above 2
check(output_q(st.interrupt()).quantity > 2, "the higher of (provider floor, tokenizer estimate) is kept — never undercount")

# --- 7. Supersession still works after a provider-floored interrupt (INV-5) ------------------
st = tracker()
st.observe_usage(input_tokens=100, output_tokens=40)
partial = st.interrupt()
final = st.resolve_with_final_usage(output_tokens=120, input_tokens=100, provider_total_tokens=220)
check(partial.superseded and partial.superseded_by == final.event_id, "the provider-floored partial is superseded by the real final")
check(partial.event_contributing_tokens == 0, "the superseded partial contributes 0")
check(final.event_contributing_tokens == 220, "only the real final usage counts")

# --- 8. END-TO-END via consume_stream: an Anthropic stream that reports cumulative output in
# message_delta, then is cut mid-flight, must interrupt using the provider's own last count ---
CTX = TraceContext(trace_id="tr-e2e", span_id="sp-e2e", request_correlation_id="corr-e2e")


def anthropic_broken_stream():
    # Anthropic splits usage: input in message_start, CUMULATIVE output in each message_delta.
    yield {"type": "message_start", "message": {"model": "claude-x", "usage": {"input_tokens": 1500, "output_tokens": 0}}}
    yield {"type": "message_delta", "usage": {"output_tokens": 10}}
    yield {"type": "message_delta", "usage": {"output_tokens": 25}}  # last count the client saw
    raise ConnectionError("client disconnected mid-stream")  # the cut


event = consume_stream(anthropic_broken_stream(), AnthropicMessagesAdapter(), context=CTX)
out = output_q(event)
iq = input_q(event)
check("stream_interrupted" in event.data_quality_flags, "e2e: a broken Anthropic stream is handled as an interruption, no crash")
check(out is not None and out.quantity == 25, "e2e: interrupt uses the LAST cumulative message_delta output (25), not a tokenizer guess")
check(out.usage_source == UsageSource.PROVIDER_STREAM_PARTIAL, "e2e: the output floor is labeled PROVIDER_STREAM_PARTIAL")
check(
    iq is not None and iq.quantity == 1500 and iq.precision_level == PrecisionLevel.EXACT,
    "e2e: the exact input from message_start is preserved (1500)",
)

sys.exit(check.report("RESULT test_stream_provider_floor"))
