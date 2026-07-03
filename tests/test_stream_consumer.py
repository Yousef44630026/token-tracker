"""Extra — stream consumer: drive the StreamTracker from real provider stream events.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_stream_consumer.py

Covers a clean OpenAI stream (final usage chunk -> EXACT), a clean Anthropic stream whose usage
is SPLIT across message_start (input) and message_delta (output), an interrupted stream (no
final usage -> ESTIMATE from text), and a stream that errors mid-iteration (-> interrupt, no crash).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def out(ev):
    return next(q for q in ev.quantities if q.token_type == TokenType.OUTPUT)


def openai_text(event):
    choices = event.get("choices") or []
    return choices[0].get("delta", {}).get("content") if choices else None


def anthropic_text(event):
    if event.get("type") == "content_block_delta":
        return event.get("delta", {}).get("text")
    return None


# ===== clean OpenAI stream (final usage chunk) =====
openai_clean = [
    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}},
]
ev = consume_stream(openai_clean, OpenAIChatCompletionsAdapter(), context=new_trace(), text_extractor=openai_text)
check(out(ev).precision_level == PrecisionLevel.EXACT and out(ev).quantity == 10, "OpenAI clean: EXACT output 10")
check(ev.event_contributing_tokens == 60 and ev.event_total_mismatch == 0, "OpenAI clean: 60, reconciles")
check("partial_stream_estimate" not in ev.data_quality_flags, "OpenAI clean: no estimate flag")

# ===== clean Anthropic stream (usage SPLIT across events) =====
anthropic_clean = [
    {"type": "message_start", "message": {"model": "claude-3-5-sonnet-20241022", "usage": {"input_tokens": 1500, "output_tokens": 1}}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Based on "}},
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "the orders..."}},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 420}},
    {"type": "message_stop"},
]
ev = consume_stream(anthropic_clean, AnthropicMessagesAdapter(), context=new_trace(), text_extractor=anthropic_text)
inp = next(q for q in ev.quantities if q.token_type == TokenType.INPUT)
check(inp.quantity == 1500, "Anthropic split: input from message_start (1500)")
check(out(ev).quantity == 420 and out(ev).precision_level == PrecisionLevel.EXACT, "Anthropic split: output from message_delta (420)")
check(ev.event_contributing_tokens == 1920, "Anthropic split: total 1500 + 420 == 1920")

# ===== clean OpenAI Responses stream preserves final subtotals =====
responses_clean = [
    {"type": "response.output_text.delta", "delta": "Hello"},
    {
        "type": "response.completed",
        "model": "o4-mini-2025-04-16",
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 40},
            "output_tokens": 20,
            "output_tokens_details": {"reasoning_tokens": 5},
            "total_tokens": 120,
        },
    },
]
ev = consume_stream(responses_clean, OpenAIResponsesAdapter(), context=new_trace())
by_type = {q.token_type: q for q in ev.quantities}
check(by_type[TokenType.INPUT].quantity == 100, "Responses stream: input exact")
check(by_type[TokenType.CACHED_INPUT].quantity == 40, "Responses stream: cached subtotal preserved")
check(by_type[TokenType.OUTPUT].quantity == 20, "Responses stream: output exact")
check(by_type[TokenType.REASONING].quantity == 5, "Responses stream: reasoning subtotal preserved")
check(ev.event_contributing_tokens == 120 and ev.event_total_mismatch == 0, "Responses stream: no double count, reconciles")
check(ev.model == "o4-mini-2025-04-16", "Responses stream: model from final event")

# ===== interrupted OpenAI stream (no final usage chunk) =====
openai_cut = [
    {"choices": [{"index": 0, "delta": {"content": "Partial answer that "}}]},
    {"choices": [{"index": 0, "delta": {"content": "never finished"}}]},
]
ev = consume_stream(openai_cut, OpenAIChatCompletionsAdapter(), context=new_trace(), text_extractor=openai_text)
check(out(ev).precision_level == PrecisionLevel.ESTIMATE and out(ev).quantity > 0, "interrupted: ESTIMATE output > 0")
check("partial_stream_estimate" in ev.data_quality_flags and "stream_interrupted" in ev.data_quality_flags, "interrupted: flagged")


# ===== stream that errors mid-iteration -> interrupt, no crash =====
def boom():
    yield {"choices": [{"index": 0, "delta": {"content": "got some text"}}]}
    raise RuntimeError("connection reset by peer")


ev = consume_stream(boom(), OpenAIChatCompletionsAdapter(), context=new_trace(), text_extractor=openai_text)
check(out(ev).precision_level == PrecisionLevel.ESTIMATE, "errored stream: handled as interruption (ESTIMATE)")
check("stream_interrupted" in ev.data_quality_flags, "errored stream: stream_interrupted flagged, no crash")


# ===== malformed TERMINAL usage (valid ingestion, but the final event construction itself
# fails, e.g. a non-integer provider total surviving an adapter's unvalidated passthrough) ->
# interrupt, no crash. This is the exact bug class normalize() was hardened against
# (normalizer.py): the defensive boundary must cover terminal-event construction too, not
# just the ingestion loop, or this exact exception escapes consume_stream() uncaught.
malformed_terminal = [
    {"choices": [{"index": 0, "delta": {"content": "hello"}}]},
    {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": "not-a-number"}},
]
ev = consume_stream(malformed_terminal, OpenAIChatCompletionsAdapter(), context=new_trace(), text_extractor=openai_text)
check(out(ev).precision_level == PrecisionLevel.ESTIMATE, "malformed terminal usage: falls back to interrupt (ESTIMATE), no crash")
check("stream_interrupted" in ev.data_quality_flags, "malformed terminal usage: stream_interrupted flagged")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
