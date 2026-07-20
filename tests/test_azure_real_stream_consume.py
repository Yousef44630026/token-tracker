"""HARD, Azure-specific: consume the REAL captured Azure stream end-to-end.

Unlike the matrix test (which cherry-picks the single usage chunk), this replays the ENTIRE
real 9-chunk Azure `chat.completions` stream through `consume_stream` — the same path the proxy
uses and the same shape a client loop sees — and asserts the produced event is EXACT and
reconciles. This is what actually processing an Azure service looks like, including Azure-only
quirks that OpenAI-direct never sends:

  * a leading chunk carrying ONLY `prompt_filter_results` (no choices, no usage),
  * a terminal usage frame with `choices: []` and `stream_options.include_usage` usage,
  * `completion_tokens_details.reasoning_tokens` on a real gpt-5-mini reasoning stream,
  * `accepted_prediction_tokens` / `rejected_prediction_tokens` predicted-output fields.

If any of those breaks extraction, fabricates tokens, or trips provider_schema_drift on
legitimate real Azure fields, this test fails.

Run: python tests/test_azure_real_stream_consume.py
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
from tracker.streaming.stream_consumer import consume_stream  # noqa: E402

check = make_checker()

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "azure_B1_stream_complete.REAL.json")
data = json.load(open(FIX, encoding="utf-8"))
check(data.get("_SIMULATED") is False, "fixture is a REAL captured Azure stream")
chunks = data["captured"]
check(isinstance(chunks, list) and len(chunks) >= 5, f"real stream has multiple chunks (got {len(chunks)})")

# The known real magnitudes captured from Azure gpt-5-mini (assert we recover THEM, not a re-derivation).
usage_chunk = next(c for c in chunks if c.get("usage"))
real = usage_chunk["usage"]
real_prompt = real["prompt_tokens"]              # 14
real_completion = real["completion_tokens"]      # 143
real_total = real["total_tokens"]                # 157
real_reasoning = real["completion_tokens_details"]["reasoning_tokens"]  # 128
check(real_prompt + real_completion == real_total, f"sanity: Azure's own numbers add up ({real_prompt}+{real_completion}={real_total})")

# Azure-only shape assertions on the raw stream (these are what OpenAI-direct never sends).
filter_only = [c for c in chunks if not c.get("choices") and not c.get("usage")]
check(len(filter_only) >= 1, "real Azure stream carries a prompt_filter_results-only chunk (Azure-specific)")
check(usage_chunk.get("choices") == [], "the terminal usage frame has empty choices (Azure include_usage shape)")


def text_of(chunk: object) -> str | None:
    if isinstance(chunk, dict) and chunk.get("choices"):
        return chunk["choices"][0].get("delta", {}).get("content")
    return None


# --- replay the WHOLE real stream through the real consumer path ---
event = consume_stream(
    chunks,
    AzureOpenAIChatCompletionsAdapter(deployment="gpt-5-mini"),
    context=new_trace(workflow="chatbot", environment="prod", business_id="support"),
    text_extractor=text_of,
    model="gpt-5-mini",
)

by_type = {q.token_type: q for q in event.quantities}
inp = by_type.get(TokenType.INPUT)
out = by_type.get(TokenType.OUTPUT)
reasoning = by_type.get(TokenType.REASONING)

# 1) exact provider-observed usage, recovered from the real terminal frame
check(inp is not None and inp.quantity == real_prompt, f"input recovered exactly from the real stream ({real_prompt})")
check(out is not None and out.quantity == real_completion, f"output recovered exactly from the real stream ({real_completion})")
check(out.precision_level == PrecisionLevel.EXACT, "streamed output is EXACT (real provider usage, not an estimate)")
check(out.usage_source.value == "provider_stream_final", "provenance is provider_stream_final (came from the stream, not a body)")

# 2) reasoning is a subtotal of output that contributes 0 — not added on top, not double-counted
check(reasoning is not None and reasoning.quantity == real_reasoning, f"reasoning tokens recovered ({real_reasoning})")
check(reasoning.subtotal_of == "output" and reasoning.quantity_in_total == 0, "reasoning is a subtotal_of output, contributes 0")

# 3) THE GROUND TRUTH: the whole real stream reconciles to Azure's own total, no double count
check(event.provider_total_tokens == real_total, f"provider_total is Azure's real total ({real_total})")
check(event.event_total_mismatch == 0, "GROUND TRUTH: sum(counted) == Azure total on a real streamed reasoning call")
check(event.event_contributing_tokens == real_prompt + real_completion, "contributing == input + output (reasoning folded, not added)")

# 4) Azure-only fields did not corrupt processing: no fabricated tokens, no false drift/missing
check(TokenType.IMAGE_INPUT not in by_type and TokenType.AUDIO_INPUT not in by_type, "no fabricated modality tokens from zero details")
check("raw_usage_missing" not in event.data_quality_flags, "usage was found in the stream (not reported missing)")
check(
    "provider_schema_drift" not in event.data_quality_flags,
    f"no false schema drift on legitimate real Azure fields (flags: {event.data_quality_flags})",
)
check(event.is_authoritative and not event.superseded, "the completed stream event is authoritative and not superseded")

raise SystemExit(check.report("RESULT test_azure_real_stream_consume"))
