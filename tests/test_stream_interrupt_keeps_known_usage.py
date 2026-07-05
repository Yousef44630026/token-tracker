"""Regression (S1) — an interrupted stream must not THROW AWAY usage it already received.

Providers split stream usage: Anthropic sends exact input tokens in message_start and the
output count only at message_delta/message_stop. If the stream dies in between,
consume_stream fell back to tracker.interrupt(), which emitted ONLY a text-based output
estimate — the exact input tokens already received from the provider were silently dropped
(an undercount of real, billed tokens), and any provider-reported cumulative output count
was discarded in favour of a poorer text estimate.

Counting the maximum reliably-known tokens means: on interrupt, keep the exact input, and
use the provider's own partial output count when it is better than the text estimate. The
enriched partial must still be superseded by the real final usage (INV-5) — carrying an
exact provider-sourced input must not stop it from being recognized as a partial.

Run: python tests/test_stream_interrupt_keeps_known_usage.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.propagation import TraceContext  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def ctx(rcid="rc-1"):
    return TraceContext(trace_id="t", span_id="s", request_correlation_id=rcid)


def get_q(event, token_type):
    matches = [q for q in event.quantities if q.token_type == token_type]
    return matches[0] if matches else None


# --- 1) StreamTracker.interrupt keeps a known exact input ---
tr = StreamTracker.from_context(ctx(), provider="anthropic", api_surface="messages")
tr.feed("Hello wor")  # ~2-3 tokens of text seen
partial = tr.interrupt(input_tokens=77)
inp = get_q(partial, TokenType.INPUT)
out = get_q(partial, TokenType.OUTPUT)
check(inp is not None and inp.quantity == 77, "interrupt keeps the exact input already received (77)")
check(
    inp is not None and inp.precision_level == PrecisionLevel.EXACT and inp.usage_source == UsageSource.PROVIDER_RESPONSE,
    "kept input is EXACT / provider-sourced (it IS provider data)",
)
check(out is not None and out.precision_level == PrecisionLevel.ESTIMATE, "output remains an ESTIMATE")
check("partial_stream_estimate" in partial.data_quality_flags, "partial flag still raised")
check(partial.event_contributing_tokens == 77 + (out.quantity or 0), "contributing = exact input + output estimate")

# --- 2) provider's cumulative output count beats a poorer text estimate ---
tr2 = StreamTracker.from_context(ctx("rc-2"), provider="anthropic", api_surface="messages")
tr2.feed("Hi")  # text estimate ~1
partial2 = tr2.interrupt(input_tokens=10, output_tokens_seen=42)
out2 = get_q(partial2, TokenType.OUTPUT)
check(out2 is not None and out2.quantity == 42, "provider's cumulative output count (42) used over the tiny text estimate")
check(out2 is not None and out2.precision_level == PrecisionLevel.ESTIMATE, "still an ESTIMATE (not final usage)")

# --- 3) the enriched partial is STILL superseded by the real final (INV-5) ---
final = tr.resolve_with_final_usage(output_tokens=200, input_tokens=77, provider_total_tokens=277)
check(partial.superseded is True, "enriched partial (with exact input) is still superseded by the final")
check(partial.event_contributing_tokens == 0, "superseded partial contributes 0 (no double count)")
check(final.event_contributing_tokens == 277, "final contributes its full usage")

# --- 4) reconcile over a list also recognizes the enriched partial ---
tr3 = StreamTracker.from_context(ctx("rc-3"), provider="anthropic", api_surface="messages")
tr3.feed("abc")
p3 = tr3.interrupt(input_tokens=50)
tr3b = StreamTracker.from_context(ctx("rc-3"), provider="anthropic", api_surface="messages")
f3 = tr3b.complete(output_tokens=99, input_tokens=50, provider_total_tokens=149)
reconcile_supersession([p3, f3])
check(p3.superseded and not f3.superseded, "reconciler pairs enriched partial with its final by rcid")
check(p3.event_contributing_tokens + f3.event_contributing_tokens == 149, "group total == final usage only")

# --- 5) consume_stream: input arrives, stream dies before output usage -> input kept ---


class _StubUsage:
    def __init__(self, quantities, total=None, model=None):
        self.quantities = quantities
        self.provider_total_tokens = total
        self.model = model
        self.data_quality_flags = []


class _StubAdapter:
    provider = "anthropic"
    api_surface = "messages"

    def extract_usage_from_stream_event(self, event):
        if event.get("type") == "message_start":
            from tracker.models.enums import Additivity
            from tracker.models.token_quantity import TokenQuantity

            return _StubUsage(
                [
                    TokenQuantity(
                        TokenType.INPUT, 123, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING
                    )
                ]
            )
        return None


def broken_stream():
    yield {"type": "message_start"}
    yield {"type": "content_block_delta", "text": "Bonjour"}
    raise ConnectionError("upstream died")


event = consume_stream(
    broken_stream(),
    _StubAdapter(),
    context=ctx("rc-stream"),
    text_extractor=lambda e: e.get("text"),
)
inp5 = get_q(event, TokenType.INPUT)
check(inp5 is not None and inp5.quantity == 123, "consume_stream: exact input (123) survives the mid-stream crash")
check(get_q(event, TokenType.OUTPUT) is not None, "consume_stream: output estimate still present")
check("partial_stream_estimate" in event.data_quality_flags, "consume_stream: flagged as partial")

# --- 6) no args -> behaviour unchanged (backward compatible) ---
tr6 = StreamTracker.from_context(ctx("rc-6"), provider="openai", api_surface="chat_completions")
tr6.feed("some text here")
p6 = tr6.interrupt()
check(get_q(p6, TokenType.INPUT) is None, "interrupt() without known input: no fabricated input quantity")
out6 = get_q(p6, TokenType.OUTPUT)
check(out6 is not None and out6.usage_source == UsageSource.PARTIAL_STREAM_TOKENIZER, "text-only estimate keeps its tokenizer source")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
