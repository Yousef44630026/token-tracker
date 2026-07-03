"""Process-local lock registry for repositories sharing one filesystem path."""

from __future__ import annotations

from threading import RLock

_PATH_LOCKS: dict[str, RLock] = {}
_PATH_LOCKS_GUARD = RLock()


def lock_for(path: str) -> RLock:
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(path, RLock())
