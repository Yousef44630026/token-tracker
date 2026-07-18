"""Regression coverage for disk-backed effective-event reconciliation at ledger scale."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.effective_events import EffectiveEventSnapshot  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402


def event(index: int, *, correlation_id: str | None = None, timestamp: str | None = None) -> TokenEvent:
    return TokenEvent(
        event_id=f"event-{index}",
        request_correlation_id=correlation_id or f"request-{index}",
        trace_id="scale-trace",
        span_id=f"span-{index}",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                1,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        timestamp=timestamp,
        observation={"authoritative": True},
    )


started = time.monotonic()
snapshot = EffectiveEventSnapshot(event(index) for index in range(4_000))
snapshot_path = snapshot.path
try:
    projected = list(snapshot)
finally:
    snapshot.close()
elapsed = time.monotonic() - started

assert len(projected) == 4_000
assert [projected[0].event_id, projected[-1].event_id] == ["event-0", "event-3999"]
assert elapsed < 20, f"4,000 independent correlations took {elapsed:.1f}s"
assert not os.path.exists(snapshot_path)

duplicate_snapshot = EffectiveEventSnapshot(
    iter(
        [
            event(4_001, correlation_id="duplicate", timestamp="2026-07-17T10:00:00Z"),
            event(4_002, correlation_id="duplicate", timestamp="2026-07-17T10:01:00Z"),
        ]
    )
)
try:
    duplicate_events = list(duplicate_snapshot)
finally:
    duplicate_snapshot.close()

assert duplicate_events[0].superseded is True
assert duplicate_events[0].superseded_by == "event-4002"
assert duplicate_events[1].superseded is False

print(f"[PASS] 4,000-correlation effective snapshot completed in {elapsed:.2f}s")
print("[PASS] effective snapshot preserves duplicate-final supersession")
