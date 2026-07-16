"""Provider usage schema drift is visible, bounded, and never auto-counted."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import DataQualityFlag  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


adapter = OpenAIResponsesAdapter()
clean = normalize(
    {
        "model": "gpt-audit",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens_details": {"reasoning_tokens": 2},
            "service_tier": "default",
        },
    },
    adapter,
    context=new_trace(),
)
check(DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value not in clean.data_quality_flags, "known token fields remain clean")
check("unmapped_usage_fields" not in clean.observation, "non-token metadata does not create drift evidence")

drifted = normalize(
    {
        "model": "gpt-audit",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "new_cache_details": {"cache_write_tokens": 9},
        },
    },
    adapter,
    context=new_trace(),
)
check(DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value in drifted.data_quality_flags, "unknown token path raises bounded drift flag")
check(drifted.event_contributing_tokens == 15, "unknown token value is never counted automatically")
check(
    drifted.observation["unmapped_usage_fields"] == ["new_cache_details.cache_write_tokens"],
    "audit observation retains the normalized unknown path",
)
check(drifted.to_dict()["data_quality_flags"] == [DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value], "drift evidence survives storage")

many_unknown = {f"new_token_counter_{index}": index for index in range(20)}
bounded = normalize(
    {"usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, **many_unknown}},
    adapter,
    context=new_trace(),
)
check(len(bounded.observation["unmapped_usage_fields"]) == 8, "unmapped path evidence is capped at eight entries")
check(bounded.data_quality_flags.count(DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value) == 1, "drift emits one low-cardinality flag")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
