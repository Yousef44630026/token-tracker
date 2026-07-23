"""Strict JSON decoding for provider Server-Sent Event responses."""

from __future__ import annotations

import json
from typing import Any


def parse_sse_json(raw: bytes) -> list[dict[str, Any]]:
    """Return JSON object payloads from an SSE body.

    Multi-line ``data:`` fields are joined according to the SSE framing rules. Comments,
    keep-alives, and the conventional ``[DONE]`` marker carry no provider usage and are
    ignored. Malformed JSON fails closed instead of silently dropping a usage event.
    """
    if not isinstance(raw, bytes):
        raise TypeError("raw SSE body must be bytes")
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    events: list[dict[str, Any]] = []
    for block in (text + "\n\n").split("\n\n"):
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[6:].strip() or None
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            continue
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ValueError("SSE data must decode to a JSON object")
        if event_name and "type" not in decoded:
            decoded["type"] = event_name
        events.append(decoded)
    return events


__all__ = ["parse_sse_json"]
