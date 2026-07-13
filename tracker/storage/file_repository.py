"""JSONL repository — writes SOURCE-OF-TRUTH fields only (INV-1 / INV-2). (Phase 2)

One JSON object per line. The repository never invents columns and never writes a
derived field: it serializes strictly via ``TokenEvent.to_dict()``, which is the single
gate that keeps derived totals out of storage.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Iterator

from tracker.models.token_event import TokenEvent
from tracker.storage._locking import lock_for

_logger = logging.getLogger(__name__)


class FileRepository:
    """Append-only JSONL store of TokenEvents."""

    def __init__(
        self,
        path: str,
        *,
        durable: bool = False,
        recover_truncated_tail: bool = True,
        skip_invalid_records: bool = True,
    ) -> None:
        self.path = os.path.abspath(path)
        self.durable = durable
        self.recover_truncated_tail = recover_truncated_tail
        self.skip_invalid_records = skip_invalid_records
        self._skipped_invalid_count = 0
        self._lock = lock_for(self.path)
        self._known_ids: set[str] | None = None
        self._known_signature: tuple[int, int] | None = None
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)

    @property
    def skipped_invalid_count(self) -> int:
        """Number of schema-invalid rows skipped by the most recent read."""
        return self._skipped_invalid_count

    def append(self, event: TokenEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[TokenEvent]) -> None:
        materialized = list(events)
        if not materialized:
            return
        if any(not isinstance(event, TokenEvent) for event in materialized):
            raise TypeError("events must contain TokenEvent objects")
        lines = [json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in materialized]
        with self._lock:
            self._append_lines_unlocked(lines)
            if self._known_ids is not None:
                self._known_ids.update(event.event_id for event in materialized)
                self._known_signature = self._file_signature_unlocked()

    def append_unique(self, events: Iterable[TokenEvent]) -> list[str]:
        """Append only event ids not already present; return the newly persisted ids."""
        materialized = list(events)
        if any(not isinstance(event, TokenEvent) for event in materialized):
            raise TypeError("events must contain TokenEvent objects")
        with self._lock:
            known = self._event_ids_unlocked()
            unique: list[TokenEvent] = []
            batch_ids: set[str] = set()
            for event in materialized:
                if event.event_id in known or event.event_id in batch_ids:
                    continue
                batch_ids.add(event.event_id)
                unique.append(event)
            if unique:
                lines = [json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in unique]
                self._append_lines_unlocked(lines)
                known.update(batch_ids)
                self._known_signature = self._file_signature_unlocked()
            return [event.event_id for event in unique]

    def read_all(self) -> list[TokenEvent]:
        return list(self.iter_events())

    def iter_events(self) -> Iterator[TokenEvent]:
        """Stream events from JSONL without materializing the whole file."""
        with self._lock:
            yield from self._iter_events_unlocked()

    def write_compacted(self, destination_path: str, *, drop_superseded: bool = True) -> int:
        """Write a compacted JSONL copy and return the number of events retained."""
        destination = os.path.abspath(destination_path)
        if destination == self.path:
            raise ValueError("destination_path must differ from the source path")
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        kept = 0
        with self._lock:
            with open(destination, "w", encoding="utf-8", newline="\n") as out:
                for event in self._iter_events_unlocked():
                    if drop_superseded and event.superseded:
                        continue
                    out.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                    kept += 1
                out.flush()
                if self.durable:
                    os.fsync(out.fileno())
        return kept

    def event_ids(self) -> set[str]:
        """Return a snapshot of persisted event ids."""
        with self._lock:
            return set(self._event_ids_unlocked())

    def _append_lines_unlocked(self, lines: list[str]) -> None:
        self._repair_tail_unlocked()
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(lines))
            fh.flush()
            if self.durable:
                os.fsync(fh.fileno())

    def _repair_tail_unlocked(self) -> None:
        """Complete or discard a crash-truncated final line before appending."""
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            return
        with open(self.path, "rb+") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return

            position = handle.tell() - 1
            line_start = 0
            while position >= 0:
                handle.seek(position)
                if handle.read(1) == b"\n":
                    line_start = position + 1
                    break
                position -= 1

            handle.seek(line_start)
            tail = handle.read()
            try:
                json.loads(tail.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                if not self.recover_truncated_tail:
                    raise
                # This cannot distinguish a genuine crash-mid-write from a line that is
                # corrupt for some other reason (disk fault, an unrelated bug) — both look
                # identical here. Recovery still discards the line (the documented,
                # opt-out-able default), but a warning makes the discard observable instead
                # of silent; use recover_truncated_tail=False for strict detection instead.
                _logger.warning(
                    "FileRepository: discarding unparseable trailing line in %s (%d bytes) "
                    "while repairing a crash-truncated tail; set recover_truncated_tail=False "
                    "to raise instead of silently recovering",
                    self.path,
                    len(tail),
                )
                handle.seek(line_start)
                handle.truncate()
            else:
                handle.seek(0, os.SEEK_END)
                handle.write(b"\n")
            handle.flush()
            if self.durable:
                os.fsync(handle.fileno())

    def _event_ids_unlocked(self) -> set[str]:
        signature = self._file_signature_unlocked()
        if self._known_ids is None or signature != self._known_signature:
            self._known_ids = {event.event_id for event in self._read_all_unlocked()}
            self._known_signature = signature
        return self._known_ids

    def _file_signature_unlocked(self) -> tuple[int, int] | None:
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def _read_all_unlocked(self) -> list[TokenEvent]:
        return list(self._iter_events_unlocked())

    def _iter_events_unlocked(self) -> Iterator[TokenEvent]:
        self._skipped_invalid_count = 0
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for index, raw_line in enumerate(fh):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    yield TokenEvent.from_dict(json.loads(line))
                except json.JSONDecodeError:
                    is_truncated_tail = not raw_line.endswith("\n")
                    if is_truncated_tail:
                        # A crash-truncated tail is governed by recover_truncated_tail ALONE:
                        # strict mode must raise even when skip_invalid_records is on, or the
                        # strict contract silently degrades into the lenient one.
                        if not self.recover_truncated_tail:
                            raise
                        # Same ambiguity as _repair_tail_unlocked: this looks like a
                        # crash-truncated line (last line, no trailing newline) but could in
                        # principle be corrupt for another reason. Silently omitting it from
                        # the read is the documented default; warn so it's not invisible.
                        _logger.warning(
                            "FileRepository: omitting unparseable trailing line %d in %s "
                            "from read_all (looks like a crash-truncated write); set "
                            "recover_truncated_tail=False to raise instead",
                            index + 1,
                            self.path,
                        )
                        self._skipped_invalid_count += 1
                        break
                    if self.skip_invalid_records:
                        self._skipped_invalid_count += 1
                        _logger.warning(
                            "FileRepository: skipping malformed JSONL row %d in %s",
                            index + 1,
                            self.path,
                        )
                        continue
                    raise
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    if not self.skip_invalid_records:
                        raise
                    self._skipped_invalid_count += 1
                    _logger.warning(
                        "FileRepository: skipping schema-invalid JSONL row %d in %s: %s: %s",
                        index + 1,
                        self.path,
                        type(exc).__name__,
                        exc,
                    )


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.=-]+")


def _safe_segment(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    segment = _SAFE_SEGMENT.sub("_", value.strip())
    return segment[:120] or fallback


def _event_date(event: TokenEvent) -> str:
    if event.timestamp and len(event.timestamp) >= 10:
        date = event.timestamp[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return date
    return "unknown-date"


class PartitionedFileRepository:
    """Date/trace partitioned JSONL repository for higher-volume observability."""

    _INDEX_FILENAME = ".event-index.sqlite3"
    _INDEX_SCHEMA_VERSION = "1"

    def __init__(
        self,
        root_dir: str,
        *,
        durable: bool = False,
        recover_truncated_tail: bool = True,
        skip_invalid_records: bool = True,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.durable = durable
        self.recover_truncated_tail = recover_truncated_tail
        self.skip_invalid_records = skip_invalid_records
        self._skipped_invalid_count = 0
        self._lock = lock_for(os.path.join(self.root_dir, ".repository"))
        self._index_path = os.path.join(self.root_dir, self._INDEX_FILENAME)
        os.makedirs(self.root_dir, exist_ok=True)

    @property
    def skipped_invalid_count(self) -> int:
        """Number of schema-invalid rows skipped by the most recent partitioned read."""
        return self._skipped_invalid_count

    @property
    def index_path(self) -> str:
        """Path to the disposable, automatically rebuilt event-id index."""
        return self._index_path

    def _path_for_event(self, event: TokenEvent) -> str:
        date = _safe_segment(_event_date(event), "unknown-date")
        trace_id = _safe_segment(event.trace_id, "unknown-trace")
        return os.path.join(self.root_dir, f"date={date}", f"trace_id={trace_id}", "events.jsonl")

    def _repo_for_path(self, path: str) -> FileRepository:
        return FileRepository(
            path,
            durable=self.durable,
            recover_truncated_tail=self.recover_truncated_tail,
            skip_invalid_records=self.skip_invalid_records,
        )

    def append(self, event: TokenEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[TokenEvent]) -> None:
        materialized = list(events)
        grouped: dict[str, list[TokenEvent]] = defaultdict(list)
        for event in materialized:
            if not isinstance(event, TokenEvent):
                raise TypeError("events must contain TokenEvent objects")
            grouped[self._path_for_event(event)].append(event)
        if not grouped:
            return
        with self._lock:
            connection = self._open_synced_index_unlocked()
            try:
                for path, group in grouped.items():
                    self._repo_for_path(path).append_many(group)
                    self._record_appended_events_unlocked(connection, path, group)
                connection.commit()
            finally:
                connection.close()

    def append_unique(self, events: Iterable[TokenEvent]) -> list[str]:
        materialized = list(events)
        if any(not isinstance(event, TokenEvent) for event in materialized):
            raise TypeError("events must contain TokenEvent objects")
        if not materialized:
            return []
        with self._lock:
            connection = self._open_synced_index_unlocked()
            try:
                known = self._known_event_ids_unlocked(
                    connection,
                    [event.event_id for event in materialized],
                )
                unique: list[TokenEvent] = []
                batch_ids: set[str] = set()
                for event in materialized:
                    if event.event_id in known or event.event_id in batch_ids:
                        continue
                    batch_ids.add(event.event_id)
                    unique.append(event)

                grouped: dict[str, list[TokenEvent]] = defaultdict(list)
                for event in unique:
                    grouped[self._path_for_event(event)].append(event)
                for path, group in grouped.items():
                    self._repo_for_path(path).append_many(group)
                    self._record_appended_events_unlocked(connection, path, group)
                connection.commit()
                return [event.event_id for event in unique]
            finally:
                connection.close()

    def iter_events(self) -> Iterator[TokenEvent]:
        self._skipped_invalid_count = 0
        for root, dirs, files in os.walk(self.root_dir):
            dirs.sort()
            for name in sorted(files):
                if name != "events.jsonl":
                    continue
                repo = self._repo_for_path(os.path.join(root, name))
                yield from repo.iter_events()
                self._skipped_invalid_count += repo.skipped_invalid_count

    def read_all(self) -> list[TokenEvent]:
        return list(self.iter_events())

    def event_ids(self) -> set[str]:
        with self._lock:
            connection = self._open_synced_index_unlocked()
            try:
                return {row[0] for row in connection.execute("SELECT DISTINCT event_id FROM event_locations")}
            finally:
                connection.close()

    def write_compacted(self, destination_root: str, *, drop_superseded: bool = True) -> int:
        """Write a partition-preserving compacted copy and return retained event count."""
        destination = os.path.abspath(destination_root)
        if destination == self.root_dir:
            raise ValueError("destination_root must differ from the source root")
        destination_repo = self.__class__(
            destination,
            durable=self.durable,
            recover_truncated_tail=self.recover_truncated_tail,
            skip_invalid_records=self.skip_invalid_records,
        )
        kept = 0
        batch: list[TokenEvent] = []
        for event in self.iter_events():
            if drop_superseded and event.superseded:
                continue
            batch.append(event)
            kept += 1
            if len(batch) >= 1000:
                destination_repo.append_many(batch)
                batch.clear()
        if batch:
            destination_repo.append_many(batch)
        return kept

    def _open_synced_index_unlocked(self) -> sqlite3.Connection:
        """Open and synchronize the disposable index, rebuilding it if corrupt."""
        for attempt in range(2):
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(self._index_path, timeout=30.0)
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(f"PRAGMA synchronous={'FULL' if self.durable else 'NORMAL'}")
                self._ensure_index_schema_unlocked(connection)
                self._sync_index_unlocked(connection)
                return connection
            except sqlite3.DatabaseError:
                if connection is not None:
                    connection.close()
                if attempt:
                    raise
                _logger.warning("PartitionedFileRepository: rebuilding corrupt index %s", self._index_path)
                self._discard_index_unlocked()
        raise RuntimeError("unreachable index recovery state")

    def _ensure_index_schema_unlocked(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS indexed_partitions (
                partition_path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_locations (
                event_id TEXT NOT NULL,
                partition_path TEXT NOT NULL,
                PRIMARY KEY (event_id, partition_path)
            );
            CREATE INDEX IF NOT EXISTS idx_event_locations_partition
                ON event_locations(partition_path);
            """
        )
        row = connection.execute(
            "SELECT value FROM index_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None and row[0] != self._INDEX_SCHEMA_VERSION:
            connection.execute("DELETE FROM event_locations")
            connection.execute("DELETE FROM indexed_partitions")
        connection.execute(
            "INSERT OR REPLACE INTO index_metadata(key, value) VALUES ('schema_version', ?)",
            (self._INDEX_SCHEMA_VERSION,),
        )
        connection.commit()

    def _sync_index_unlocked(self, connection: sqlite3.Connection) -> None:
        current: dict[str, tuple[int, int]] = {}
        for path in self._partition_paths():
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                continue
            current[self._relative_partition_path(path)] = (stat.st_size, stat.st_mtime_ns)

        indexed = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT partition_path, size_bytes, mtime_ns FROM indexed_partitions"
            )
        }
        for relative_path in indexed.keys() - current.keys():
            connection.execute("DELETE FROM event_locations WHERE partition_path = ?", (relative_path,))
            connection.execute("DELETE FROM indexed_partitions WHERE partition_path = ?", (relative_path,))
        for relative_path, signature in current.items():
            if indexed.get(relative_path) == signature:
                continue
            self._reindex_partition_unlocked(connection, relative_path)
        connection.commit()

    def _reindex_partition_unlocked(self, connection: sqlite3.Connection, relative_path: str) -> None:
        path = os.path.join(self.root_dir, relative_path)
        repository = self._repo_for_path(path)
        connection.execute("DELETE FROM event_locations WHERE partition_path = ?", (relative_path,))
        with repository._lock:
            rows = ((event.event_id, relative_path) for event in repository._iter_events_unlocked())
            connection.executemany(
                "INSERT OR IGNORE INTO event_locations(event_id, partition_path) VALUES (?, ?)",
                rows,
            )
            signature = repository._file_signature_unlocked()
        if signature is None:
            connection.execute("DELETE FROM indexed_partitions WHERE partition_path = ?", (relative_path,))
            return
        connection.execute(
            """
            INSERT OR REPLACE INTO indexed_partitions(partition_path, size_bytes, mtime_ns)
            VALUES (?, ?, ?)
            """,
            (relative_path, signature[0], signature[1]),
        )

    def _record_appended_events_unlocked(
        self,
        connection: sqlite3.Connection,
        path: str,
        events: Iterable[TokenEvent],
    ) -> None:
        relative_path = self._relative_partition_path(path)
        connection.executemany(
            "INSERT OR IGNORE INTO event_locations(event_id, partition_path) VALUES (?, ?)",
            ((event.event_id, relative_path) for event in events),
        )
        stat = os.stat(path)
        connection.execute(
            """
            INSERT OR REPLACE INTO indexed_partitions(partition_path, size_bytes, mtime_ns)
            VALUES (?, ?, ?)
            """,
            (relative_path, stat.st_size, stat.st_mtime_ns),
        )

    def _known_event_ids_unlocked(
        self,
        connection: sqlite3.Connection,
        event_ids: Iterable[str],
    ) -> set[str]:
        distinct_ids = list(dict.fromkeys(event_ids))
        known: set[str] = set()
        for offset in range(0, len(distinct_ids), 900):
            chunk = distinct_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            known.update(
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT event_id FROM event_locations WHERE event_id IN ({placeholders})",
                    chunk,
                )
            )
        return known

    def _partition_paths(self) -> Iterator[str]:
        for root, dirs, files in os.walk(self.root_dir):
            dirs.sort()
            if "events.jsonl" in files:
                yield os.path.join(root, "events.jsonl")

    def _relative_partition_path(self, path: str) -> str:
        return os.path.relpath(path, self.root_dir)

    def _discard_index_unlocked(self) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(f"{self._index_path}{suffix}")
            except FileNotFoundError:
                pass
