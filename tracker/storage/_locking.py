"""Re-entrant process-local and cross-process repository locks.

The lock file is deliberately persistent.  Ownership is held by the operating system, not
by the file's existence, so a crashed process cannot leave a stale lock behind and no
process ever unlinks a lock file another process may already have open.
"""

from __future__ import annotations

import errno
import math
import os
import time
from threading import RLock, get_ident

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_DEFAULT_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 0.01
_PATH_LOCKS: dict[str, InterProcessRLock] = {}
_PATH_LOCKS_GUARD = RLock()


def _canonical_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class InterProcessRLock:
    """An ``RLock`` combined with a kernel-managed exclusive file lock.

    The process-local ``RLock`` provides thread ownership and re-entrancy.  The outermost
    acquisition also locks one byte of a stable sidecar file, which serializes independent
    Python processes.  File descriptors are closed on release and by the OS on process
    termination, so stale lock-file contents never imply stale ownership.
    """

    def __init__(self, target_path: str, *, default_timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        if (
            isinstance(default_timeout_seconds, bool)
            or not isinstance(default_timeout_seconds, (int, float))
            or not math.isfinite(default_timeout_seconds)
            or default_timeout_seconds < 0
        ):
            raise ValueError("default_timeout_seconds must be a finite non-negative number")
        self.target_path = _canonical_path(target_path)
        self.lock_path = f"{self.target_path}.lock"
        self.default_timeout_seconds = float(default_timeout_seconds)
        self._thread_lock = RLock()
        self._depth = 0
        self._descriptor: int | None = None
        self._owner_thread_id: int | None = None

    def acquire(self, *, timeout_seconds: float | None = None) -> bool:
        """Acquire the lock or raise ``TimeoutError`` after a bounded wait."""

        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout_seconds must be a finite non-negative number")
        timeout = float(timeout)
        deadline = time.monotonic() + timeout
        if not self._thread_lock.acquire(timeout=timeout):
            raise self._timeout_error(timeout)

        try:
            if self._depth:
                self._depth += 1
                return True

            descriptor = self._open_lock_file()
            try:
                while True:
                    try:
                        self._try_lock_descriptor(descriptor)
                        break
                    except OSError as exc:
                        if not self._lock_is_contended(exc):
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise self._timeout_error(timeout) from exc
                        time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
            except BaseException:
                os.close(descriptor)
                raise

            self._descriptor = descriptor
            self._depth = 1
            self._owner_thread_id = get_ident()
            return True
        except BaseException:
            self._thread_lock.release()
            raise

    def release(self) -> None:
        """Release one recursion level and the OS lock at the outermost level."""

        if self._depth <= 0 or self._owner_thread_id != get_ident():
            raise RuntimeError("cannot release an un-acquired repository lock")
        self._depth -= 1
        if self._depth:
            self._thread_lock.release()
            return

        descriptor = self._descriptor
        self._descriptor = None
        self._owner_thread_id = None
        try:
            if descriptor is not None:
                try:
                    self._unlock_descriptor(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            self._thread_lock.release()

    def __enter__(self) -> InterProcessRLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.release()

    def _open_lock_file(self) -> int:
        parent = os.path.dirname(self.lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        return descriptor

    @staticmethod
    def _try_lock_descriptor(descriptor: int) -> None:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_descriptor(descriptor: int) -> None:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _lock_is_contended(exc: OSError) -> bool:
        return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(exc, "winerror", None) in {33, 36}

    def _timeout_error(self, timeout: float) -> TimeoutError:
        return TimeoutError(f"timed out after {timeout:.3f}s acquiring repository lock for {self.target_path}")


def lock_for(path: str) -> InterProcessRLock:
    """Return the shared re-entrant lock for one canonical filesystem target."""

    canonical = _canonical_path(path)
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(canonical, InterProcessRLock(canonical))
