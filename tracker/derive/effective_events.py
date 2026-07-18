"""Canonical correlation-effective projection over immutable source events.

Raw JSONL rows never need to be rewritten when a later final usage event arrives. Every
aggregate instead consumes this projection, which derives quality flags and supersession from
the complete correlation group. Sequence inputs use an in-memory copy; one-shot iterators use a
temporary SQLite snapshot so memory grows with the largest correlation group, not the ledger.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from itertools import groupby

from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.normalization.quality_flags import normalize_quality_flags
from tracker.normalization.reconciler import reconcile_events


class EffectiveEventList(list[TokenEvent]):
    """Internal marker for an already cloned and reconciled event sequence.

    Aggregation bundles reuse this immutable-by-convention projection across metrics. The
    marker prevents each metric from cloning and reconciling the same large trace again.
    """


def _clone(event: TokenEvent) -> TokenEvent:
    return TokenEvent.from_dict(event.to_dict())


def effective_events(events: Iterable[TokenEvent]) -> list[TokenEvent]:
    """Return deduplicated, reconciled copies without mutating source events."""
    if isinstance(events, EffectiveEventList):
        return events
    seen: set[str] = set()
    copied: list[TokenEvent] = []
    for event in events:
        if not isinstance(event, TokenEvent):
            raise TypeError("events must contain TokenEvent objects")
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        copied.append(_clone(event))
    return EffectiveEventList(reconcile_events(copied))


class EffectiveEventSnapshot:
    """Disk-backed, replayable effective event view for a one-shot source."""

    def __init__(self, events: Iterable[TokenEvent]) -> None:
        descriptor, self.path = tempfile.mkstemp(prefix=".tracker-effective-", suffix=".sqlite3")
        os.close(descriptor)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute(
            """
            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                request_correlation_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                superseded INTEGER NOT NULL DEFAULT 0,
                superseded_by TEXT,
                quality_flags TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        self._connection.execute("CREATE INDEX idx_effective_correlation ON events(request_correlation_id, sequence)")
        self._connection.execute(
            """
            CREATE TABLE effective_state (
                sequence INTEGER PRIMARY KEY,
                superseded INTEGER NOT NULL,
                superseded_by TEXT,
                quality_flags TEXT NOT NULL,
                FOREIGN KEY(sequence) REFERENCES events(sequence)
            )
            """
        )
        try:
            with self._connection:
                for event in events:
                    if not isinstance(event, TokenEvent):
                        raise TypeError("events must contain TokenEvent objects")
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO events(
                            event_id, request_correlation_id, payload, quality_flags
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            event.request_correlation_id,
                            json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")),
                            json.dumps(event.data_quality_flags, ensure_ascii=False, separators=(",", ":")),
                        ),
                    )
            self._reconcile()
        except Exception:
            self.close()
            raise

    def _reconcile(self) -> None:
        rows = self._connection.execute(
            """
            SELECT sequence, request_correlation_id, payload
            FROM events
            ORDER BY request_correlation_id, sequence
            """
        )
        with self._connection:
            for _, correlation_rows in groupby(rows, key=lambda row: row[1]):
                materialized_rows = list(correlation_rows)
                group = [TokenEvent.from_dict(json.loads(payload)) for _, _, payload in materialized_rows]
                reconcile_events(group)
                self._connection.executemany(
                    """
                    INSERT INTO effective_state(sequence, superseded, superseded_by, quality_flags)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            sequence,
                            1 if event.superseded else 0,
                            event.superseded_by,
                            json.dumps(event.data_quality_flags, ensure_ascii=False, separators=(",", ":")),
                        )
                        for (sequence, _, _), event in zip(materialized_rows, group, strict=True)
                    ],
                )

    def __iter__(self) -> Iterator[TokenEvent]:
        cursor = self._connection.execute(
            """
            SELECT events.payload, effective_state.superseded,
                   effective_state.superseded_by, effective_state.quality_flags
            FROM events
            JOIN effective_state USING(sequence)
            ORDER BY events.sequence
            """
        )
        for payload, superseded, superseded_by, quality_flags in cursor:
            event = TokenEvent.from_dict(json.loads(payload))
            event.superseded = bool(superseded)
            event.superseded_by = superseded_by
            event.data_quality_flags = normalize_quality_flags(json.loads(quality_flags))
            yield event

    def close(self) -> None:
        self._connection.close()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> EffectiveEventSnapshot:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


def iter_effective_events(events: Iterable[TokenEvent]) -> Iterator[TokenEvent]:
    """Yield the canonical effective view for sequence or streaming event sources."""
    if isinstance(events, Sequence):
        yield from effective_events(events)
        return
    snapshot = EffectiveEventSnapshot(events)
    try:
        yield from snapshot
    finally:
        snapshot.close()


def effective_trace(trace: Trace) -> Trace:
    """Return a trace copy whose events carry freshly derived effective state."""
    projected = Trace(
        trace_id=trace.trace_id,
        business_id=trace.business_id,
        workflow=trace.workflow,
        environment=trace.environment,
        spans=list(trace.spans),
        events=effective_events(trace.events),
    )
    # Trace validates and rebuilds its event list during construction. Restore the internal
    # marker after that validation so every export metric can reuse this one projection.
    projected.events = EffectiveEventList(projected.events)
    return projected


__all__ = [
    "EffectiveEventSnapshot",
    "EffectiveEventList",
    "effective_events",
    "effective_trace",
    "iter_effective_events",
]
