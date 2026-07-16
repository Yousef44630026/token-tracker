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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter
from tracker.models.token_event import TokenEvent

SessionSnapshot = dict[str, int]


@dataclass
class ClaudeImportReport:
    """Audit counters for one Claude Code transcript import."""

    session_files_discovered: int = 0
    files_scanned: int = 0
    lines_scanned: int = 0
    malformed_json_lines: int = 0
    assistant_records: int = 0
    usage_objects: int = 0
    missing_request_ids: int = 0
    duplicate_request_ids: int = 0
    events_imported: int = 0
    io_errors: int = 0
    rewound_files: int = 0
    checkpoint: SessionSnapshot = field(default_factory=dict, repr=False)

    @property
    def warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.assistant_records > 0 and self.usage_objects == 0:
            warnings.append("assistant_records_without_usage_objects")
        if self.usage_objects > 0 and self.events_imported == 0:
            warnings.append("usage_objects_without_imported_events")
        if self.usage_objects > 0 and self.missing_request_ids == self.usage_objects:
            warnings.append("all_usage_objects_missing_request_id")
        if self.lines_scanned >= 10 and self.malformed_json_lines * 2 >= self.lines_scanned:
            warnings.append("high_malformed_json_rate")
        if self.io_errors:
            warnings.append("session_file_io_errors")
        return warnings

    @property
    def format_drift_suspected(self) -> bool:
        return bool(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("checkpoint", None)
        return {
            **values,
            "warnings": self.warnings,
            "format_drift_suspected": self.format_drift_suspected,
        }


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


def _line_events(
    *,
    path: Path,
    start_offset: int,
    seen_request_ids: set[str],
    report: ClaudeImportReport,
) -> tuple[list[TokenEvent], int]:
    """Read complete lines from one byte offset and return the exact consumed offset."""
    adapter = AnthropicMessagesAdapter()
    first_line_number = _line_number_at_offset(path, start_offset)
    events: list[TokenEvent] = []
    consumed_offset = start_offset
    with path.open("rb") as handle:
        handle.seek(max(start_offset, 0))
        for _line_number, raw_bytes in enumerate(handle, start=first_line_number):
            # Claude writes transcripts concurrently. Never advance the checkpoint past an
            # incomplete tail; the next run will retry it after the writer adds the newline.
            if not raw_bytes.endswith(b"\n"):
                break
            consumed_offset = handle.tell()
            report.lines_scanned += 1
            line = raw_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                report.malformed_json_lines += 1
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") != "assistant":
                continue
            message = item.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            report.assistant_records += 1
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            report.usage_objects += 1
            request_id = item.get("requestId")
            if not request_id:
                # No stable id to de-duplicate content-block lines against; skip rather than
                # risk counting the same turn more than once.
                report.missing_request_ids += 1
                continue
            dedup_key = f"{path}:{request_id}"
            if dedup_key in seen_request_ids:
                report.duplicate_request_ids += 1
                continue
            seen_request_ids.add(dedup_key)

            normalized = adapter.extract_usage_from_response({"model": message.get("model"), "usage": usage})
            timestamp = item.get("timestamp")
            session_id = item.get("sessionId") or path.stem
            event_id = _event_id(path, request_id)
            event = TokenEvent(
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
                    "status": "complete",
                    "source": "claude_code_session_log",
                    "session_id": session_id,
                    "session_file": path.name,
                    "request_id": request_id,
                    "is_sidechain": bool(item.get("isSidechain", False)),
                    "entrypoint": item.get("entrypoint"),
                },
            )
            report.events_imported += 1
            events.append(event)
    return events, consumed_offset


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
    events, _report = import_new_claude_code_events_with_report(
        before=before,
        claude_home=claude_home,
        only_new_sessions=only_new_sessions,
    )
    return events


def import_new_claude_code_events_with_report(
    *,
    before: SessionSnapshot | None = None,
    claude_home: str | os.PathLike[str] | None = None,
    only_new_sessions: bool = False,
) -> tuple[list[TokenEvent], ClaudeImportReport]:
    """Import events and return canary counters for transcript-format drift."""
    home = Path(claude_home) if claude_home else default_claude_home()
    projects_root = home / "projects"
    report = ClaudeImportReport(checkpoint=dict(before or {}))
    if not projects_root.exists():
        return [], report
    prior = before or {}
    events: list[TokenEvent] = []
    seen_request_ids: set[str] = set()
    paths = sorted(projects_root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    report.session_files_discovered = len(paths)
    for path in paths:
        if only_new_sessions and str(path) in prior:
            continue
        start_offset = prior.get(str(path), 0)
        try:
            size = path.stat().st_size
            if size < start_offset:
                # Transcript rotation/truncation invalidates the old byte offset. Rewind;
                # deterministic event ids and collector de-duplication make replay safe.
                start_offset = 0
                report.rewound_files += 1
            if size == start_offset:
                continue
            report.files_scanned += 1
            file_events, consumed_offset = _line_events(
                path=path,
                start_offset=start_offset,
                seen_request_ids=seen_request_ids,
                report=report,
            )
            events.extend(file_events)
            report.checkpoint[str(path)] = consumed_offset
        except OSError:
            report.io_errors += 1
            continue
    return events, report
