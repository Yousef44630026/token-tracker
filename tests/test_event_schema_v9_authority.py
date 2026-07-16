"""Event schema v9 stores source facts only and fails closed on missing authority."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import CURRENT_EVENT_SCHEMA_VERSION, TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.observability.observation import Observation  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


quantity = TokenQuantity(
    TokenType.INPUT,
    10,
    PrecisionLevel.EXACT,
    UsageSource.PROVIDER_RESPONSE,
    Additivity.TOTAL_CONTRIBUTING,
)
implicit = TokenEvent(
    event_id="implicit",
    request_correlation_id="request-implicit",
    trace_id="trace",
    span_id="span",
    quantities=[quantity],
)
check(implicit.is_authoritative is False, "omitted low-level authority fails closed")
check(isinstance(implicit.observation, Observation), "authority gate remains a typed Observation object")
check(isinstance(implicit.observation.authoritative, bool), "typed authority is a real boolean field")
check(implicit.event_contributing_tokens == 0, "implicit authority cannot enter totals")
check("authority_missing" in implicit.data_quality_flags, "implicit authority is auditable")

explicit = TokenEvent(
    event_id="explicit",
    request_correlation_id="request-explicit",
    trace_id="trace",
    span_id="span",
    quantities=[quantity],
    observation={"authoritative": True, "status": "complete"},
    superseded=True,
    superseded_by="replacement",
    data_quality_flags=["provider_total_mismatch", "superseded", "raw_usage_missing"],
)
stored = explicit.to_dict()
stored_quantity = stored["quantities"][0]
check(stored["schema_version"] == CURRENT_EVENT_SCHEMA_VERSION == 9, "every JSONL event carries schema version 9")
check("superseded" not in stored and "superseded_by" not in stored, "derived supersession state is not stored")
check(stored["data_quality_flags"] == ["raw_usage_missing"], "derivable quality flags are not stored")
check("additivity" not in stored_quantity, "legacy flat additivity is no longer redundantly stored")
check(stored_quantity["overlap"] == "independent" and stored_quantity["trust"] == "verified", "v9 stores the two real axes")

round_trip = TokenEvent.from_dict(stored)
check(round_trip.is_authoritative is True, "explicit authority survives v9 round-trip")
check(round_trip.quantities[0].additivity == Additivity.TOTAL_CONTRIBUTING, "legacy additivity view is derived on read")

legacy = {
    "event_id": "legacy",
    "request_correlation_id": "request-legacy",
    "trace_id": "trace",
    "span_id": "span",
    "quantities": [],
}
legacy_event = TokenEvent.from_dict(legacy)
check(legacy_event.is_authoritative is False, "legacy row without observation loads fail-closed")
check("authority_missing" in legacy_event.data_quality_flags, "legacy authority gap remains visible")

strict_rejected = False
try:
    TokenEvent.from_dict({**legacy, "schema_version": 9}, require_explicit_authority=True)
except ValueError:
    strict_rejected = True
check(strict_rejected, "live ingestion rejects v9 payloads without explicit authority")

unsupported_rejected = False
try:
    TokenEvent.from_dict({**stored, "schema_version": 999})
except ValueError:
    unsupported_rejected = True
check(unsupported_rejected, "unsupported event schema versions fail closed")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
