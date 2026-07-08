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
    ) -> None:
        self.path = os.path.abspath(path)
        self.durable = durable
        self.recover_truncated_tail = recover_truncated_tail
        self._lock = lock_for(self.path)
        self._known_ids: set[str] | None = None
        self._known_signature: tuple[int, int] | None = None
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)

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
                    if self.recover_truncated_tail and is_truncated_tail:
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
                        break
                    raise


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

    def __init__(
        self,
        root_dir: str,
        *,
        durable: bool = False,
        recover_truncated_tail: bool = True,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.durable = durable
        self.recover_truncated_tail = recover_truncated_tail
        os.makedirs(self.root_dir, exist_ok=True)

    def _path_for_event(self, event: TokenEvent) -> str:
        date = _safe_segment(_event_date(event), "unknown-date")
        trace_id = _safe_segment(event.trace_id, "unknown-trace")
        return os.path.join(self.root_dir, f"date={date}", f"trace_id={trace_id}", "events.jsonl")

    def _repo_for_path(self, path: str) -> FileRepository:
        return FileRepository(path, durable=self.durable, recover_truncated_tail=self.recover_truncated_tail)

    def append(self, event: TokenEvent) -> None:
        self.append_many([event])

    def append_many(self, events: Iterable[TokenEvent]) -> None:
        grouped: dict[str, list[TokenEvent]] = defaultdict(list)
        for event in events:
            if not isinstance(event, TokenEvent):
                raise TypeError("events must contain TokenEvent objects")
            grouped[self._path_for_event(event)].append(event)
        for path, group in grouped.items():
            self._repo_for_path(path).append_many(group)

    def append_unique(self, events: Iterable[TokenEvent]) -> list[str]:
        appended: list[str] = []
        grouped: dict[str, list[TokenEvent]] = defaultdict(list)
        for event in events:
            if not isinstance(event, TokenEvent):
                raise TypeError("events must contain TokenEvent objects")
            grouped[self._path_for_event(event)].append(event)
        for path, group in grouped.items():
            appended.extend(self._repo_for_path(path).append_unique(group))
        return appended

    def iter_events(self) -> Iterator[TokenEvent]:
        for root, dirs, files in os.walk(self.root_dir):
            dirs.sort()
            for name in sorted(files):
                if name != "events.jsonl":
                    continue
                yield from self._repo_for_path(os.path.join(root, name)).iter_events()

    def read_all(self) -> list[TokenEvent]:
        return list(self.iter_events())

    def event_ids(self) -> set[str]:
        return {event.event_id for event in self.iter_events()}

    def write_compacted(self, destination_root: str, *, drop_superseded: bool = True) -> int:
        """Write a partition-preserving compacted copy and return retained event count."""
        destination = os.path.abspath(destination_root)
        if destination == self.root_dir:
            raise ValueError("destination_root must differ from the source root")
        kept = 0
        for event in self.iter_events():
            if drop_superseded and event.superseded:
                continue
            self.__class__(
                destination,
                durable=self.durable,
                recover_truncated_tail=self.recover_truncated_tail,
            ).append(event)
            kept += 1
        return kept
