"""Atomic persistence for a complete Trace, including spans and events."""

from __future__ import annotations

import json
import os
import tempfile

from tracker.models.trace import Trace
from tracker.storage._locking import lock_for

SCHEMA_VERSION = 1


class TraceFileRepository:
    """Store one complete trace snapshot as an atomically replaced JSON document."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self._parent = os.path.dirname(os.path.abspath(path))
        self._lock = lock_for(self.path)
        os.makedirs(self._parent, exist_ok=True)

    def save(self, trace: Trace) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "trace": trace.to_dict(),
        }
        with self._lock:
            temporary_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self._parent,
                    prefix=".trace-",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary_path = handle.name
                    json.dump(payload, handle, ensure_ascii=False)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.path)
                self._sync_parent_directory()
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    os.remove(temporary_path)

    def load(self) -> Trace | None:
        with self._lock:
            if not os.path.exists(self.path):
                return None
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
            version = payload.get("schema_version")
            if version != SCHEMA_VERSION:
                raise ValueError(f"unsupported trace schema_version: {version!r}")
            return Trace.from_dict(payload["trace"])

    def _sync_parent_directory(self) -> None:
        """Persist the rename on filesystems that allow directory fsync."""
        if os.name == "nt":
            return
        descriptor = os.open(self._parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
