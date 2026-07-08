"""Persistence for a complete Trace, including spans and events.

Uses atomic replace where the filesystem allows it. Some managed Windows/OneDrive folders let
Python write files but deny rename/delete of temporary files; those environments fall back to a
single durable write so trace snapshots remain usable instead of failing mid-observation.
"""

from __future__ import annotations

import json
import os
import time
import uuid

from tracker.models.trace import Trace
from tracker.storage._locking import lock_for

SCHEMA_VERSION = 1
_WINDOWS_RETRY_DELAYS_SECONDS = (0.01, 0.05, 0.1, 0.25, 0.5)


class TraceFileRepository:
    """Store one complete trace snapshot as a JSON document."""

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
            if os.name == "nt":
                self._write_payload(self.path, payload)
                self._sync_parent_directory()
                return

            temporary_path: str | None = None
            try:
                temporary_path = os.path.join(self._parent, f".trace-{os.getpid()}-{uuid.uuid4().hex}.tmp")
                self._write_payload(temporary_path, payload)
                self._replace_with_retries(temporary_path, self.path)
                self._sync_parent_directory()
            finally:
                if temporary_path and os.path.exists(temporary_path):
                    self._remove_with_retries(temporary_path)

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

    @staticmethod
    def _write_payload(path: str, payload: dict) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _replace_with_retries(source: str, destination: str) -> None:
        for delay in (*_WINDOWS_RETRY_DELAYS_SECONDS, None):
            try:
                os.replace(source, destination)
                return
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)

    @staticmethod
    def _remove_with_retries(path: str) -> None:
        for delay in (*_WINDOWS_RETRY_DELAYS_SECONDS, None):
            try:
                os.remove(path)
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if delay is None:
                    raise
                time.sleep(delay)
