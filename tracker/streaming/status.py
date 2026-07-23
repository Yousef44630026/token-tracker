"""Merge provider stream lifecycle states without hiding an earlier failure."""

from __future__ import annotations

_STATUS_PRIORITY = {
    None: 0,
    "complete": 1,
    "incomplete": 2,
    "failed": 3,
}


def merge_stream_status(current: str | None, incoming: str | None) -> str | None:
    """Keep the most severe lifecycle state observed across split stream chunks."""
    if incoming is None:
        return current
    if current is None:
        return incoming
    current_priority = _STATUS_PRIORITY.get(current, 0)
    incoming_priority = _STATUS_PRIORITY.get(incoming, 0)
    return incoming if incoming_priority > current_priority else current
