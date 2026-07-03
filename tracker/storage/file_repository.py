"""JSONL repository — writes SOURCE-OF-TRUTH fields only (INV-1 / INV-2). (Phase 2)

One JSON object per line. The repository never invents columns and never writes a
derived field: it serializes strictly via ``TokenEvent.to_dict()``, which is the single
gate that keeps derived totals out of storage.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable

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
        with self._lock:
            return self._read_all_unlocked()

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
        if not os.path.exists(self.path):
            return []
        out: list[TokenEvent] = []
        with open(self.path, encoding="utf-8") as fh:
            lines = fh.readlines()
            for index, raw_line in enumerate(lines):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    out.append(TokenEvent.from_dict(json.loads(line)))
                except json.JSONDecodeError:
                    is_truncated_tail = index == len(lines) - 1 and not raw_line.endswith("\n")
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
        return out
