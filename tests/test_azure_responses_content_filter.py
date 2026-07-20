"""HARD, Azure-specific: a REAL Azure *Responses API* call blocked by content filtering.

Two Azure-only things at once that OpenAI-direct chat completions never send:
  * the Responses API usage shape (`input_tokens` / `output_tokens`, not prompt/completion),
  * a content-filter outcome — Azure filtered the visible content but the model still consumed
    tokens (here 704 reasoning tokens), which Azure reports and bills.

The failure mode this guards against: dropping a "filtered/blocked" call as an error and counting
0 tokens. Azure consumed real tokens; the tracker must count them exactly and record the outcome,
not silently zero them.

Run: python tests/test_azure_responses_content_filter.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "realistic", "azure_content_filter_block_completed.REAL.json")
data = json.load(open(FIX, encoding="utf-8"))
body = data["response"]

# Azure-specific shape assertions on the raw payload.
check("content_filters" in body, "real payload carries Azure content_filters metadata")
usage = body["usage"]
check("input_tokens" in usage and "output_tokens" in usage, "Responses API usage shape (input/output_tokens), not chat's prompt/completion")
real_input = usage["input_tokens"]            # 31
real_output = usage["output_tokens"]          # 1088
real_total = usage["total_tokens"]            # 1119
real_reasoning = usage["output_tokens_details"]["reasoning_tokens"]  # 704
check(real_input + real_output == real_total and real_reasoning > 0, "sanity: Azure's numbers add up and reasoning was consumed")

ctx = new_trace(workflow="moderated-gen", environment="prod", business_id="safety")
event = normalize(body, AzureOpenAIResponsesAdapter(deployment="gpt-5-mini"), context=ctx)
by_type = {q.token_type: q for q in event.quantities}
inp = by_type.get(TokenType.INPUT)
out = by_type.get(TokenType.OUTPUT)
reasoning = by_type.get(TokenType.REASONING)

# 1) the filtered call's tokens are COUNTED, exactly — not dropped, not zeroed
check(inp is not None and inp.quantity == real_input, f"input counted despite the filter ({real_input})")
check(out is not None and out.quantity == real_output, f"output counted despite the filter ({real_output})")
check(out.precision_level == PrecisionLevel.EXACT, "the filtered call's usage is EXACT provider data")
check(event.event_contributing_tokens == real_input + real_output, "a content-filtered call is billed and counted, not treated as 0")

# 2) reasoning tokens (the bulk here) are a subtotal contributing 0 — counted once, in output
check(reasoning is not None and reasoning.quantity == real_reasoning, f"reasoning tokens recovered ({real_reasoning})")
check(reasoning.subtotal_of == "output" and reasoning.quantity_in_total == 0, "reasoning is a subtotal_of output, contributes 0")

# 3) GROUND TRUTH: reconciles to Azure's own total, no double count
check(event.provider_total_tokens == real_total, f"provider_total is Azure's real total ({real_total})")
check(event.event_total_mismatch == 0, "GROUND TRUTH: sum(counted) == Azure total on a real content-filtered Responses call")

# 4) no false alarms on legitimate real Azure Responses fields
check("provider_schema_drift" not in event.data_quality_flags, f"no false schema drift on Responses fields ({event.data_quality_flags})")
check("raw_usage_missing" not in event.data_quality_flags, "usage was present and read (not reported missing)")

raise SystemExit(check.report("RESULT test_azure_responses_content_filter"))
