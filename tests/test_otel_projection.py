"""OpenTelemetry GenAI projection preserves the tracker's accounting semantics."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.export.otel_projection import (  # noqa: E402
    TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES,
    TOKEN_USAGE_METRIC_NAME,
    TOKEN_USAGE_UNIT,
    record_token_usage,
    token_usage_measurements,
)
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)["response"]


def values(event):
    return {item.attributes["gen_ai.token.type"]: item.value for item in token_usage_measurements(event)}


openai = normalize(load("openai_responses_cached_reasoning.SIMULATED.json"), OpenAIResponsesAdapter(), context=new_trace())
check(values(openai) == {"input": 1000, "output": 300}, "OpenAI cache/reasoning subtotals are not duplicated")

bedrock = normalize(load("bedrock_converse_cache.SIMULATED.json"), BedrockConverseAdapter(), context=new_trace())
bedrock_measurements = token_usage_measurements(bedrock)
check(values(bedrock) == {"input": 1920, "output": 300}, "Bedrock cache buckets roll into standard input usage")
check(
    all(item.attributes["gen_ai.provider.name"] == "aws.bedrock" for item in bedrock_measurements),
    "Bedrock uses the standard provider name",
)

anthropic = normalize(load("anthropic_messages_cache.SIMULATED.json"), AnthropicMessagesAdapter(), context=new_trace())
check(values(anthropic) == {"input": 1920, "output": 300}, "Anthropic thinking subtotal does not inflate output")


class Histogram:
    def __init__(self):
        self.records = []

    def record(self, value, *, attributes):
        self.records.append((value, attributes))


histogram = Histogram()
check(record_token_usage(bedrock, histogram) == 2 and len(histogram.records) == 2, "duck-typed OTel histogram receives both observations")
bedrock.superseded = True
bedrock.superseded_by = "replacement"
check(record_token_usage(bedrock, histogram) == 0, "superseded events emit no standard metrics")
check(TOKEN_USAGE_METRIC_NAME == "gen_ai.client.token.usage" and TOKEN_USAGE_UNIT == "{token}", "metric identity follows GenAI conventions")
check(len(TOKEN_USAGE_EXPLICIT_BUCKET_BOUNDARIES) == 14, "recommended explicit token boundaries are exposed")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
