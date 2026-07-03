"""Import Claude Code CLI token usage from local session transcripts.

Claude Code writes one JSONL file per session under ``~/.claude/projects/<project>/*.jsonl``.
Each assistant turn's ``message.usage`` is the exact Anthropic Messages usage object
(``input_tokens``, ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
``output_tokens``). This importer maps those local events into the tracker model through the
real ``AnthropicMessagesAdapter`` — without reading raw prompt/assistant text.

Critical de-duplication: a single API turn is split across MULTIPLE JSONL lines (one per
content block — thinking / text / tool_use), and EVERY line repeats a verbatim copy of that
turn's ``usage`` object under the same ``requestId``. Counting every line would double- (or
triple-) count tokens. This importer keeps exactly ONE event per ``requestId``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter
from tracker.models.token_event import TokenEvent

SessionSnapshot = dict[str, int]


def default_claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def snapshot_sessions(claude_home: str | os.PathLike[str] | None = None) -> SessionSnapshot:
    """Return current byte sizes for Claude Code session JSONL files (all projects)."""
    projects_root = (Path(claude_home) if claude_home else default_claude_home()) / "projects"
    if not projects_root.exists():
        return {}
    return {str(path): path.stat().st_size for path in projects_root.rglob("*.jsonl")}


def _event_id(path: Path, request_id: str) -> str:
    payload = json.dumps({"path": str(path), "request_id": request_id}, sort_keys=True)
    return "claude-code-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _line_number_at_offset(path: Path, offset: int) -> int:
    if offset <= 0:
        return 1
    with path.open("rb") as handle:
        return handle.read(offset).count(b"\n") + 1


def _line_events(*, path: Path, start_offset: int, seen_request_ids: set[str]) -> Iterable[TokenEvent]:
    adapter = AnthropicMessagesAdapter()
    first_line_number = _line_number_at_offset(path, start_offset)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(max(start_offset, 0))
        for _line_number, raw_line in enumerate(handle, start=first_line_number):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "assistant":
                continue
            message = item.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            request_id = item.get("requestId")
            if not request_id:
                # No stable id to de-duplicate content-block lines against; skip rather than
                # risk counting the same turn more than once.
                continue
            dedup_key = f"{path}:{request_id}"
            if dedup_key in seen_request_ids:
                continue
            seen_request_ids.add(dedup_key)

            normalized = adapter.extract_usage_from_response({"model": message.get("model"), "usage": usage})
            timestamp = item.get("timestamp")
            session_id = item.get("sessionId") or path.stem
            event_id = _event_id(path, request_id)
            yield TokenEvent(
                event_id=event_id,
                request_correlation_id=event_id,
                trace_id=session_id,
                span_id=f"claude-code-{request_id}",
                workflow="claude_code_local_usage",
                environment="local",
                provider=normalized.provider,
                model=normalized.model,
                api_surface=normalized.api_surface,
                quantities=normalized.quantities,
                provider_total_tokens=normalized.provider_total_tokens,
                data_quality_flags=[*normalized.data_quality_flags, "claude_code_local_usage"],
                timestamp=timestamp if isinstance(timestamp, str) else None,
                observation={
                    "authoritative": True,
                    "status": "claude_code_local_usage",
                    "source": "claude_code_session_log",
                    "session_id": session_id,
                    "session_file": path.name,
                    "request_id": request_id,
                    "is_sidechain": bool(item.get("isSidechain", False)),
                    "entrypoint": item.get("entrypoint"),
                },
            )


def import_new_claude_code_events(
    *,
    before: SessionSnapshot | None = None,
    claude_home: str | os.PathLike[str] | None = None,
    only_new_sessions: bool = False,
) -> list[TokenEvent]:
    """Import Claude Code token-usage events created after a snapshot.

    If ``before`` is omitted, imports every assistant turn from every session (all projects).
    When ``only_new_sessions`` is true, files that already existed in ``before`` are skipped.
    De-duplicates by ``requestId`` so a turn split across multiple content-block lines is
    counted exactly once.
    """
    home = Path(claude_home) if claude_home else default_claude_home()
    projects_root = home / "projects"
    if not projects_root.exists():
        return []
    prior = before or {}
    events: list[TokenEvent] = []
    seen_request_ids: set[str] = set()
    for path in sorted(projects_root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        if only_new_sessions and str(path) in prior:
            continue
        start_offset = prior.get(str(path), 0)
        try:
            if path.stat().st_size <= start_offset:
                continue
            events.extend(_line_events(path=path, start_offset=start_offset, seen_request_ids=seen_request_ids))
        except OSError:
            continue
    return events
