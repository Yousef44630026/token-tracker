"""HARD, Azure-specific: a REAL Azure stream cut before the usage frame.

Azure sends token usage only in a terminal `include_usage` frame. If the connection drops
mid-stream (timeout, client cancel, gateway reset), that frame never arrives. This replays the
real captured Azure stream WITHOUT its terminal usage chunk — exactly what a dropped Azure
stream looks like — through the real consumer, and asserts the tracker fails HONESTLY:

  * output becomes an ESTIMATE (from the text actually seen), never presented as exact,
  * the event is flagged as interrupted,
  * unknown is never silently turned into a confident zero (INV-6),
  * and — critically — a later real final usage for the same call SUPERSEDES the estimate,
    so a retry/late-arrival never double-counts.

Run: python tests/test_azure_real_stream_cut.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

check = make_checker()

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "azure_B1_stream_complete.REAL.json")
chunks = json.load(open(FIX, encoding="utf-8"))["captured"]

# Simulate the drop: keep every chunk UP TO (not including) the terminal usage frame.
cut = []
for chunk in chunks:
    if chunk.get("usage"):
        break
    cut.append(chunk)
check(len(cut) >= 3 and not any(c.get("usage") for c in cut), "cut stream keeps the delta chunks but no usage frame")
real_usage = next(c["usage"] for c in chunks if c.get("usage"))


def text_of(chunk: object) -> str | None:
    if isinstance(chunk, dict) and chunk.get("choices"):
        return chunk["choices"][0].get("delta", {}).get("content")
    return None


ctx = new_trace(workflow="chatbot", environment="prod", business_id="support")


def run(stream: list) -> object:
    adapter = AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini")
    return consume_stream(stream, adapter, context=ctx, text_extractor=text_of, model="gpt-5-mini")


partial = run(cut)
out = next((q for q in partial.quantities if q.token_type == TokenType.OUTPUT), None)

# 1) honest degradation: estimate, flagged, never a confident exact/zero
check(out is not None, "the cut stream still produces an output quantity (from text seen), not nothing")
check(out.precision_level == PrecisionLevel.ESTIMATE, "cut-stream output is an ESTIMATE, never marked EXACT")
check(out.usage_source.value != "provider_stream_final", "cut-stream output is NOT labelled provider-final")
check("stream_interrupted" in partial.data_quality_flags, "the cut is flagged stream_interrupted")
check(partial.provider_total_tokens is None, "no provider total was received on a cut stream (not fabricated)")

# 2) supersession: the real final usage for the SAME call must supersede the estimate (no double count)
final = run(chunks)
group = reconcile_supersession([partial, final])
resolved = {e.event_id: e for e in group}
check(resolved[partial.event_id].superseded, "the interrupted estimate is superseded by the real final usage")
check(resolved[partial.event_id].event_contributing_tokens == 0, "the superseded estimate contributes 0")
check(not resolved[final.event_id].superseded, "the real final usage is the surviving event")
check(
    sum(e.event_contributing_tokens for e in group) == real_usage["prompt_tokens"] + real_usage["completion_tokens"],
    "GROUND TRUTH: partial + final counts the real final ONLY (14+143), never the estimate on top",
)

raise SystemExit(check.report("RESULT test_azure_real_stream_cut"))
