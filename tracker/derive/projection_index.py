"""Incremental, persisted effective-projection index (scale chantier S1-S3).

A reconstructible sidecar SQLite that mirrors the effective view of the JSONL ledger
(`iter_effective_events`) but is maintained INCREMENTALLY: on refresh only the newly appended
bytes are parsed, and only the correlation groups those new events touch are re-reconciled.

Correctness rests on one property of the model: reconciliation (supersession + normalizer-owned
quality flags) is LOCAL to a `request_correlation_id` group and idempotent. A newly appended
event can therefore only change the effective state of its own group, so re-running the SAME
`reconcile_events` over that group's full membership reproduces the full-scan result exactly.

This index is a cache, NEVER source of truth (INV-1/INV-2): it stores only a projection of the
ledger and can be dropped and rebuilt at any time. Any inconsistency (active file shrank, its
prefix changed, or the archive set changed — truncation/rotation/rewrite) forces a full rebuild,
which is exactly today's `iter_effective_events` path and the correctness floor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Iterable, Iterator

from tracker.derive.effective_events import iter_effective_events
from tracker.models.token_event import TokenEvent
from tracker.normalization.quality_flags import normalize_quality_flags
from tracker.normalization.reconciler import reconcile_events
from tracker.storage.file_repository import FileRepository

_logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "1"
_PREFIX_CHECK_BYTES = 65_536
_BUSY_TIMEOUT_MS = 2_000
DISABLE_FLAG = "TRACKER_DISABLE_PROJECTION_INDEX"


def _archive_signature(repo: FileRepository) -> str:
    parts: list[tuple[str, int]] = []
    for path in repo._archive_paths_unlocked():
        try:
            parts.append((os.path.basename(path), os.path.getsize(path)))
        except FileNotFoundError:
            continue
    return json.dumps(sorted(parts), separators=(",", ":"))


def _prefix_hash(path: str, upto: int) -> str:
    if upto <= 0 or not os.path.exists(path):
        return ""
    hasher = hashlib.sha256()
    remaining = min(upto, _PREFIX_CHECK_BYTES)
    with open(path, "rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(remaining, 65_536))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    return hasher.hexdigest()


class ProjectionIndex:
    """Persisted effective-projection of a JSONL ledger, maintained incrementally."""

    def __init__(self, store: str, *, index_path: str | None = None) -> None:
        self.store = os.path.abspath(store)
        self.index_path = index_path or f"{self.store}.projection.sqlite3"
        self._repo = FileRepository(self.store)

    # --- connection / schema ------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=_BUSY_TIMEOUT_MS / 1000)
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return connection

    def discard(self) -> None:
        """Delete the sidecar (and its WAL companions). The next refresh rebuilds."""
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.index_path + suffix)
            except FileNotFoundError:
                pass

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                rcid TEXT NOT NULL,
                payload TEXT NOT NULL,
                superseded INTEGER NOT NULL DEFAULT 0,
                superseded_by TEXT,
                quality_flags TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_events_rcid ON events(rcid)")
        connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    @staticmethod
    def _meta_get(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _meta_set(connection: sqlite3.Connection, **values: str) -> None:
        connection.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            list(values.items()),
        )

    # --- public API ---------------------------------------------------------
    def rebuild(self) -> None:
        """Project the whole ledger from scratch. The correctness floor and fallback."""
        connection = self._connect()
        try:
            connection.execute("DROP TABLE IF EXISTS events")
            connection.execute("DROP TABLE IF EXISTS meta")
            self._ensure_schema(connection)
            with connection:
                for event in iter_effective_events(self._repo.iter_events()):
                    self._insert_effective(connection, event)
                self._record_cursor(connection, consumed_full=True)
        finally:
            connection.close()

    def refresh(self) -> None:
        """Bring the index up to date, incrementally where possible."""
        if not os.path.exists(self.index_path):
            self.rebuild()
            return
        needs_rebuild = False
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            if self._meta_get(connection, "schema_version") != _SCHEMA_VERSION or not self._cursor_is_valid(connection):
                needs_rebuild = True
            else:
                self._apply_new_bytes(connection)
        finally:
            connection.close()
        if needs_rebuild:
            self.rebuild()

    def iter_effective_events(self) -> Iterator[TokenEvent]:
        """Yield the effective view in ledger order, reconstructed from the index."""
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            cursor = connection.execute(
                "SELECT payload, superseded, superseded_by, quality_flags FROM events ORDER BY sequence"
            )
            for payload, superseded, superseded_by, quality_flags in cursor:
                event = TokenEvent.from_dict(json.loads(payload))
                event.superseded = bool(superseded)
                event.superseded_by = superseded_by
                event.data_quality_flags = normalize_quality_flags(json.loads(quality_flags))
                yield event
        finally:
            connection.close()

    # --- internals ----------------------------------------------------------
    def _insert_effective(self, connection: sqlite3.Connection, event: TokenEvent) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO events(event_id, rcid, payload, superseded, superseded_by, quality_flags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.request_correlation_id,
                json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")),
                1 if event.superseded else 0,
                event.superseded_by,
                json.dumps(event.data_quality_flags, ensure_ascii=False, separators=(",", ":")),
            ),
        )

    def _active_size(self) -> int:
        try:
            return os.path.getsize(self.store)
        except FileNotFoundError:
            return 0

    def _cursor_is_valid(self, connection: sqlite3.Connection) -> bool:
        cursor_raw = self._meta_get(connection, "cursor_bytes")
        if cursor_raw is None:
            return False
        cursor = int(cursor_raw)
        if self._active_size() < cursor:
            return False  # active file shrank (truncation / rotation)
        if self._prefix_hash_for(cursor) != (self._meta_get(connection, "prefix_hash") or ""):
            return False  # active file prefix rewritten
        if _archive_signature(self._repo) != (self._meta_get(connection, "archive_sig") or ""):
            return False  # archive set changed (rotation)
        return True

    def _prefix_hash_for(self, cursor: int) -> str:
        return _prefix_hash(self.store, cursor)

    def _record_cursor(self, connection: sqlite3.Connection, *, consumed_full: bool, cursor: int | None = None) -> None:
        if consumed_full:
            cursor = self._last_line_boundary(0, self._active_size())
        assert cursor is not None
        self._meta_set(
            connection,
            schema_version=_SCHEMA_VERSION,
            cursor_bytes=str(cursor),
            prefix_hash=self._prefix_hash_for(cursor),
            archive_sig=_archive_signature(self._repo),
        )

    def _last_line_boundary(self, start: int, end: int) -> int:
        """Largest offset <= end that sits just after a newline in [start, end)."""
        if end <= start or not os.path.exists(self.store):
            return start
        with open(self.store, "rb") as handle:
            handle.seek(start)
            chunk = handle.read(end - start)
        last_nl = chunk.rfind(b"\n")
        return start if last_nl == -1 else start + last_nl + 1

    def _apply_new_bytes(self, connection: sqlite3.Connection) -> None:
        cursor = int(self._meta_get(connection, "cursor_bytes") or "0")
        size = self._active_size()
        if size <= cursor:
            return  # nothing new
        boundary = self._last_line_boundary(cursor, size)
        if boundary <= cursor:
            return  # only a partial (unterminated) tail line so far; wait for it to complete
        with open(self.store, "rb") as handle:
            handle.seek(cursor)
            chunk = handle.read(boundary - cursor)
        touched: set[str] = set()
        with connection:
            for raw in chunk.split(b"\n"):
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = TokenEvent.from_dict(json.loads(line.decode("utf-8")))
                except (ValueError, TypeError, KeyError, AttributeError, UnicodeDecodeError):
                    continue  # skip malformed rows (matches FileRepository's lenient default)
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO events(event_id, rcid, payload, superseded, superseded_by, quality_flags) "
                    "VALUES (?, ?, ?, 0, NULL, ?)",
                    (
                        event.event_id,
                        event.request_correlation_id,
                        json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")),
                        json.dumps(event.data_quality_flags, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
                if inserted.rowcount:
                    touched.add(event.request_correlation_id)
            for rcid in touched:
                self._reconcile_group(connection, rcid)
            self._record_cursor(connection, consumed_full=False, cursor=boundary)

    def _reconcile_group(self, connection: sqlite3.Connection, rcid: str) -> None:
        rows = connection.execute(
            "SELECT sequence, payload FROM events WHERE rcid = ? ORDER BY sequence",
            (rcid,),
        ).fetchall()
        group = [TokenEvent.from_dict(json.loads(payload)) for _, payload in rows]
        reconcile_events(group)
        connection.executemany(
            "UPDATE events SET superseded = ?, superseded_by = ?, quality_flags = ? WHERE sequence = ?",
            [
                (
                    1 if event.superseded else 0,
                    event.superseded_by,
                    json.dumps(event.data_quality_flags, ensure_ascii=False, separators=(",", ":")),
                    sequence,
                )
                for (sequence, _), event in zip(rows, group, strict=True)
            ],
        )


def _full_scan(store: str, fallback_source: Iterable[TokenEvent] | None) -> Iterator[TokenEvent]:
    source = fallback_source if fallback_source is not None else FileRepository(store).iter_events()
    yield from iter_effective_events(source)


def effective_events_for_store(
    store: str,
    *,
    fallback_source: Iterable[TokenEvent] | None = None,
) -> Iterator[TokenEvent]:
    """Yield the effective view for a single-file JSONL store via the incremental index.

    The index is a pure acceleration cache: on the disable flag, a corrupt/unusable sidecar,
    or ANY index error, this falls back to the full-scan ``iter_effective_events`` path so the
    reported numbers are byte-identical either way. A locked sidecar (another poll writing) is
    tolerated — the read proceeds on the current state and catches up on the next refresh.
    """
    if os.environ.get(DISABLE_FLAG):
        yield from _full_scan(store, fallback_source)
        return
    try:
        index = ProjectionIndex(store)
        try:
            index.refresh()
        except sqlite3.OperationalError as exc:  # e.g. "database is locked": read current state
            _logger.debug("projection index busy for %s (%s); reading current state", store, exc)
        except sqlite3.DatabaseError:  # corrupt/unreadable sidecar: discard and rebuild once
            _logger.warning("projection index for %s was unreadable; rebuilding", store)
            index.discard()
            index.rebuild()
    except Exception:
        _logger.warning("projection index unusable for %s; using full scan", store, exc_info=True)
        yield from _full_scan(store, fallback_source)
        return
    started = False
    try:
        for event in index.iter_effective_events():
            started = True
            yield event
    except Exception:
        if started:
            # Some rows were already emitted; falling back now would double-count into a
            # silently-wrong total. Fail loud instead — never a confident wrong number.
            raise
        _logger.warning("projection index read failed for %s; using full scan", store, exc_info=True)
        yield from _full_scan(store, fallback_source)


__all__ = ["ProjectionIndex", "effective_events_for_store", "DISABLE_FLAG"]
