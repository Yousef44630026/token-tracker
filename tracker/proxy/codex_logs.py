"""Import Codex CLI token-count events from local session logs.

Codex records token usage in its local session JSONL files as ``event_msg`` entries
with ``payload.type == "token_count"``. This importer maps those local events into
the tracker model without storing raw prompts or assistant text.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter
from tracker.models.token_event import TokenEvent

SessionSnapshot = dict[str, int]


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def snapshot_sessions(codex_home: str | os.PathLike[str] | None = None) -> SessionSnapshot:
    """Return current byte sizes for Codex session JSONL files."""
    sessions_root = (Path(codex_home) if codex_home else default_codex_home()) / "sessions"
    if not sessions_root.exists():
        return {}
    return {str(path): path.stat().st_size for path in sessions_root.rglob("*.jsonl")}


def _session_id_from_path(path: Path) -> str:
    stem = path.stem
    marker = "rollout-"
    if stem.startswith(marker):
        parts = stem[len(marker) :].split("-")
        if len(parts) >= 6:
            return "-".join(parts[-5:])
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32]


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _openai_usage_from_codex(usage: dict[str, Any]) -> dict[str, Any] | None:
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    if input_tokens is None or output_tokens is None or total_tokens is None:
        return None
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {
            "cached_tokens": _safe_int(usage.get("cached_input_tokens")) or 0,
        },
        "output_tokens": output_tokens,
        "output_tokens_details": {
            "reasoning_tokens": _safe_int(usage.get("reasoning_output_tokens")) or 0,
        },
        "total_tokens": total_tokens,
    }


def _thread_models(codex_home: Path) -> dict[str, dict[str, Any]]:
    state_db = codex_home / "state_5.sqlite"
    if not state_db.exists():
        return {}
    try:
        con = sqlite3.connect(str(state_db))
        try:
            rows = con.execute("select id, model, model_provider, tokens_used from threads").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {
        str(thread_id): {
            "model": model,
            "model_provider": provider,
            "tokens_used": tokens_used,
        }
        for thread_id, model, provider, tokens_used in rows
    }


def _event_id(path: Path, line_number: int, timestamp: str | None, usage: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "path": str(path),
            "line": line_number,
            "timestamp": timestamp,
            "usage": usage,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "codex-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _line_number_at_offset(path: Path, offset: int) -> int:
    if offset <= 0:
        return 1
    with path.open("rb") as handle:
        return handle.read(offset).count(b"\n") + 1


def _line_events(
    *,
    path: Path,
    start_offset: int,
    model_by_thread: dict[str, dict[str, Any]],
) -> Iterable[TokenEvent]:
    session_id = _session_id_from_path(path)
    model_info = model_by_thread.get(session_id, {})
    model = model_info.get("model")
    adapter = OpenAIResponsesAdapter()
    first_line_number = _line_number_at_offset(path, start_offset)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(max(start_offset, 0))
        for line_number, raw_line in enumerate(handle, start=first_line_number):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "event_msg":
                continue
            payload = item.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            codex_usage = info.get("last_token_usage")
            if not isinstance(codex_usage, dict):
                continue
            usage = _openai_usage_from_codex(codex_usage)
            if usage is None:
                continue
            normalized = adapter.extract_usage_from_response(
                {
                    "model": model,
                    "usage": usage,
                }
            )
            timestamp = item.get("timestamp")
            event_id = _event_id(path, line_number, timestamp, usage)
            yield TokenEvent(
                event_id=event_id,
                request_correlation_id=event_id,
                trace_id=session_id,
                span_id=f"codex-token-count-{line_number}",
                workflow="codex_local_usage",
                environment="local",
                provider=normalized.provider,
                model=normalized.model,
                api_surface=normalized.api_surface,
                quantities=normalized.quantities,
                provider_total_tokens=normalized.provider_total_tokens,
                data_quality_flags=[
                    *normalized.data_quality_flags,
                    "codex_local_token_count",
                ],
                timestamp=timestamp if isinstance(timestamp, str) else None,
                observation={
                    "authoritative": True,
                    "status": "codex_local_token_count",
                    "source": "codex_session_log",
                    "session_id": session_id,
                    "session_file": path.name,
                    "line_number": line_number,
                    "codex_payload_type": "token_count",
                    "codex_total_token_usage": info.get("total_token_usage"),
                    "codex_thread_tokens_used": model_info.get("tokens_used"),
                    "codex_model_provider": model_info.get("model_provider"),
                },
            )


def import_new_codex_events(
    *,
    before: SessionSnapshot | None = None,
    codex_home: str | os.PathLike[str] | None = None,
    only_new_sessions: bool = False,
) -> list[TokenEvent]:
    """Import Codex token-count events created after a snapshot.

    If ``before`` is omitted, imports every token-count event from all sessions.
    When ``only_new_sessions`` is true, files that already existed in ``before`` are
    ignored; this avoids attributing concurrent Codex activity from another session to
    a prompt-suite child process.
    """
    home = Path(codex_home) if codex_home else default_codex_home()
    sessions_root = home / "sessions"
    if not sessions_root.exists():
        return []
    model_by_thread = _thread_models(home)
    prior = before or {}
    events: list[TokenEvent] = []
    for path in sorted(sessions_root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        if only_new_sessions and str(path) in prior:
            continue
        start_offset = prior.get(str(path), 0)
        try:
            if path.stat().st_size <= start_offset:
                continue
            events.extend(
                _line_events(
                    path=path,
                    start_offset=start_offset,
                    model_by_thread=model_by_thread,
                )
            )
        except OSError:
            continue
    return events
