"""Provider usage schema drift is visible, bounded, and never auto-counted."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import DataQualityFlag  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.proxy.server import _UsageAccumulator  # noqa: E402

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

class SDKUsage:
    def __init__(self):
        self.input_tokens = 10
        self.output_tokens = 5
        self.total_tokens = 15
        self.future_sdk_tokens = 3

    def model_dump(self):
        return dict(self.__dict__)


class SDKResponse:
    model = "gpt-sdk"
    usage = SDKUsage()


sdk_drift = normalize(SDKResponse(), adapter, context=new_trace())
check(
    DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value in sdk_drift.data_quality_flags,
    "SDK usage objects receive the same schema-drift protection as decoded JSON",
)
check(
    sdk_drift.observation.get("unmapped_usage_fields") == ["future_sdk_tokens"],
    "SDK drift evidence retains field names without serializing the provider object",
)

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

proxy_stream = _UsageAccumulator(adapter)
proxy_stream.feed(
    {
        "usage": {
            "input_tokens": 8,
            "output_tokens": 2,
            "total_tokens": 10,
            "future_tokens": 4,
        }
    }
)
proxy_event = proxy_stream.build_event(
    context=new_trace(),
    request_hash="request-hash",
    response_hash="response-hash",
    observation={
        "authoritative": True,
        "status": "complete",
    },
)
check(proxy_event is not None and "provider_schema_drift" in proxy_event.data_quality_flags, "proxy stream runs the same drift gate")
check(
    proxy_event is not None and proxy_event.observation.get("unmapped_usage_fields") == ["future_tokens"],
    "proxy stream persists bounded drift evidence",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
