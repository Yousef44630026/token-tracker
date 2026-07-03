"""Safe-failure collector client (Phase 8).

The collector buffers token events locally and flushes them to a transport in batches. Its
one hard rule: a tracker/transport failure must NEVER raise into the calling application —
tracking is best-effort and must not break the request it is observing.

Properties:
  - non-blocking record() with an in-memory buffer bounded by ``max_buffer_size``;
  - a drop policy (drop_oldest / drop_newest) when the buffer is full, with a dropped count;
  - batch flush with a per-flush ``collector_timeout_ms`` enforced via a worker thread, so a
    slow/hung transport cannot block the caller past the timeout;
  - failed / un-acked events are kept for retry; a timed-out send remains in-flight until
    its late result is resolved or ``max_inflight_ms`` expires;
  - bounded dedup by ``event_id`` for pending and recently delivered events;
  - partial batch acks: only the un-acked events are retried;
  - ``offline_mode``: buffer only, never touch the transport.

The transport is any callable ``transport(batch: list[dict]) -> list[str]`` returning the
acked event_ids. It may raise, hang, or ack a subset; the collector tolerates all three.
Transports should be idempotent by event_id because an expired hung send can be retried.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, RLock, Thread
from time import monotonic
from typing import Any

Transport = Callable[[list[dict[str, Any]]], list[str]]


@dataclass
class CollectorConfig:
    """Tunables for the collector's buffering / flushing / drop behavior."""

    max_buffer_size: int = 1000
    batch_size: int = 100
    collector_timeout_ms: int = 2000
    offline_mode: bool = False
    drop_policy: str = "drop_oldest"  # or "drop_newest"
    dedup_history_size: int = 10000
    max_inflight_ms: int = 30000

    def __post_init__(self) -> None:
        if self.max_buffer_size <= 0:
            raise ValueError("max_buffer_size must be positive")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.collector_timeout_ms <= 0:
            raise ValueError("collector_timeout_ms must be positive")
        if self.dedup_history_size < 0:
            raise ValueError("dedup_history_size cannot be negative")
        if self.max_inflight_ms < self.collector_timeout_ms:
            raise ValueError("max_inflight_ms must be >= collector_timeout_ms")
        if self.drop_policy not in {"drop_oldest", "drop_newest"}:
            raise ValueError("drop_policy must be 'drop_oldest' or 'drop_newest'")


@dataclass
class FlushResult:
    """Outcome of one flush. ``ok`` is True only if a batch was fully acked."""

    ok: bool
    sent: int = 0
    retried: int = 0
    reason: str | None = None


@dataclass
class _InflightSend:
    batch: list[dict[str, Any]]
    worker: Thread
    result: dict[str, Any]
    started_at: float


class CollectorClient:
    """Non-blocking, fail-safe collector. No method raises into the caller."""

    def __init__(
        self,
        transport: Transport | None = None,
        config: CollectorConfig | None = None,
    ):
        self._transport = transport
        self.config = config or CollectorConfig()
        self._buffer: list[dict[str, Any]] = []
        self._pending_ids: set[str] = set()
        self._delivered_ids: set[str] = set()
        self._delivered_order: deque[str] = deque()
        self._inflight: _InflightSend | None = None
        self._inflight_ids: set[str] = set()
        self._abandoned: list[_InflightSend] = []
        self._state_lock = RLock()
        self._flush_lock = Lock()
        self.dropped_total = 0
        self.sent_total = 0

    # --- introspection ----------------------------------------------------------------
    @property
    def pending(self) -> int:
        with self._state_lock:
            return len(self._buffer)

    # --- ingest (never raises) --------------------------------------------------------
    def record(self, event: Any) -> bool:
        """Buffer one event. Returns True if accepted, False if dropped. Never raises."""
        try:
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            event_id = payload.get("event_id")
            with self._state_lock:
                if event_id is not None and (not isinstance(event_id, str) or not event_id.strip()):
                    self.dropped_total += 1
                    return False
                if event_id is not None and (
                    event_id in self._pending_ids or event_id in self._inflight_ids or event_id in self._delivered_ids
                ):
                    return False  # dedup: already buffered/sent
                if len(self._buffer) >= self.config.max_buffer_size:
                    if not self._make_room():
                        self.dropped_total += 1
                        return False
                self._buffer.append(payload)
                if event_id is not None:
                    self._pending_ids.add(event_id)
                return True
        except Exception:  # noqa: BLE001 — best-effort, must never raise
            with self._state_lock:
                self.dropped_total += 1
            return False

    def _make_room(self) -> bool:
        """Drop one event per the drop policy to make room. Returns True if room was made."""
        if not self._buffer:
            return True
        indexes = range(len(self._buffer) - 1, -1, -1)
        if self.config.drop_policy == "drop_oldest":
            indexes = range(len(self._buffer))
        drop_index = next(
            (index for index in indexes if self._buffer[index].get("event_id") not in self._inflight_ids),
            None,
        )
        if drop_index is None:
            return False
        dropped = self._buffer.pop(drop_index)
        event_id = dropped.get("event_id")
        if event_id is not None:
            self._pending_ids.discard(event_id)
        self.dropped_total += 1
        return True

    # --- flush (never raises) ---------------------------------------------------------
    def flush(self) -> FlushResult:
        """Send up to one batch. Failures requeue the batch; never raises."""
        if not self._flush_lock.acquire(blocking=False):
            return FlushResult(ok=False, reason="busy")
        try:
            with self._state_lock:
                if self.config.offline_mode or self._transport is None:
                    return FlushResult(ok=False, reason="offline")
                inflight_result = self._resolve_inflight()
                if inflight_result is not None:
                    return inflight_result
                if not self._buffer:
                    return FlushResult(ok=True, reason="empty")
                # Never re-send an event a still-running abandoned (zombie) worker might
                # still be delivering — otherwise a slow-but-not-dead transport could
                # receive the same batch twice concurrently.
                reserved = self._abandoned_ids()
                candidates = [e for e in self._buffer if e.get("event_id") not in reserved]
                if not candidates:
                    return FlushResult(ok=True, reason="empty")
                batch = list(candidates[: self.config.batch_size])
                self._inflight_ids = reserved | {event.get("event_id") for event in batch if event.get("event_id") is not None}

            acked, status, inflight = self._send_with_timeout(batch)
            with self._state_lock:
                if inflight is not None:
                    self._inflight = inflight
                else:
                    self._inflight_ids = self._abandoned_ids()
                return self._apply_send_result(batch, acked, status)
        except Exception as exc:  # noqa: BLE001 — must never raise
            with self._state_lock:
                if self._inflight is None:
                    self._inflight_ids = self._abandoned_ids()
            return FlushResult(ok=False, reason=f"error:{type(exc).__name__}")
        finally:
            self._flush_lock.release()

    def _resolve_inflight(self) -> FlushResult | None:
        """Resolve a previously timed-out send without retransmitting its batch."""
        self._reap_abandoned()
        if self._inflight is None:
            return None
        if self._inflight.worker.is_alive():
            elapsed_ms = (monotonic() - self._inflight.started_at) * 1000
            if elapsed_ms >= self.config.max_inflight_ms:
                # The transport never returned in time and Python threads cannot be
                # killed. Keep tracking the zombie worker instead of dropping it: its
                # event_ids stay reserved (never resent, never evicted by _make_room)
                # until it finishes, and its eventual result — success or failure — is
                # still applied via _reap_abandoned() rather than silently discarded.
                self._abandoned.append(self._inflight)
                self._inflight = None
                self._inflight_ids = self._abandoned_ids()
                return None
            return FlushResult(
                ok=False,
                retried=len(self._inflight.batch),
                reason="in_flight",
            )
        inflight = self._inflight
        self._inflight = None
        self._inflight_ids = self._abandoned_ids()
        return self._apply_send_result(
            inflight.batch,
            inflight.result["acked"],
            inflight.result["status"],
        )

    def _reap_abandoned(self) -> None:
        """Apply late results from abandoned sends that have since finished, so a
        legitimately-delivered late ack is honored instead of silently lost."""
        if not self._abandoned:
            return
        still_running = []
        for inflight in self._abandoned:
            if inflight.worker.is_alive():
                still_running.append(inflight)
                continue
            self._apply_send_result(inflight.batch, inflight.result["acked"], inflight.result["status"])
        self._abandoned = still_running

    def _abandoned_ids(self) -> set[str]:
        """event_ids still owned by a zombie (abandoned but possibly still running) send."""
        ids: set[str] = set()
        for inflight in self._abandoned:
            ids |= {event.get("event_id") for event in inflight.batch if event.get("event_id") is not None}
        return ids

    def _apply_send_result(
        self,
        batch: list[dict[str, Any]],
        acked: list[str],
        status: str,
    ) -> FlushResult:
        """Apply only acknowledgements belonging to the submitted batch."""
        batch_ids = {event.get("event_id") for event in batch if event.get("event_id") is not None}
        acked_set = set(acked) & batch_ids
        self._buffer = [event for event in self._buffer if event.get("event_id") not in acked_set]
        for event_id in acked_set:
            self._pending_ids.discard(event_id)
            self._remember_delivered(event_id)
        self.sent_total += len(acked_set)

        retried = len(batch) - len(acked_set)
        ok = status == "ok" and retried == 0
        reason = status if not ok else None
        return FlushResult(ok=ok, sent=len(acked_set), retried=retried, reason=reason)

    def _remember_delivered(self, event_id: str) -> None:
        if self.config.dedup_history_size == 0:
            return
        self._delivered_ids.add(event_id)
        self._delivered_order.append(event_id)
        while len(self._delivered_order) > self.config.dedup_history_size:
            expired = self._delivered_order.popleft()
            self._delivered_ids.discard(expired)

    def _send_with_timeout(self, batch: list[dict[str, Any]]) -> tuple[list[str], str, _InflightSend | None]:
        """Run the transport in a worker thread, bounded by collector_timeout_ms.

        Returns ``(acked_ids, status)`` where status is "ok", "timeout", or "error". A timed
        out or failed send acks nothing, so the whole batch is retried (idempotent by id).
        """
        result: dict[str, Any] = {"acked": [], "status": "error"}

        def _run() -> None:
            try:
                transport = self._transport
                acked = transport(batch) if transport is not None else []
                result["acked"] = list(acked) if acked else []
                result["status"] = "ok"
            except Exception:  # noqa: BLE001 — captured, surfaced as a failed flush
                result["status"] = "error"

        worker = Thread(target=_run, daemon=True)
        worker.start()
        worker.join(self.config.collector_timeout_ms / 1000.0)
        if worker.is_alive():
            inflight = _InflightSend(
                batch=list(batch),
                worker=worker,
                result=result,
                started_at=monotonic(),
            )
            return [], "timeout", inflight
        return result["acked"], result["status"], None
