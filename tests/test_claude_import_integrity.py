"""Claude transcript imports must fail loudly on drift and keep stable source identity."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import DataQualityFlag  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.proxy.claude_code_logs import (  # noqa: E402
    import_new_claude_code_events,
    import_new_claude_code_events_with_report,
)
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or Path.cwd() / f".test_claude_integrity_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)


def assistant_line(request_id: str, usage: dict[str, int], *, session_id: str = "stable-session") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "requestId": request_id,
            "sessionId": session_id,
            "timestamp": "2026-07-17T10:00:00Z",
            "message": {"role": "assistant", "model": "claude-test", "usage": usage},
        }
    )


def write_session(home: Path, project_name: str, lines: list[str]) -> Path:
    path = home / "projects" / project_name / "session.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


try:
    partial_home = work / "partial"
    write_session(
        partial_home,
        "project-a",
        [assistant_line("partial", {"input_token_count": 500, "output_tokens": 100})],
    )
    partial_events, partial_report = import_new_claude_code_events_with_report(claude_home=partial_home)
    partial = partial_events[0]
    check(partial.event_contributing_tokens == 100, "an unknown input bucket is never guessed or auto-counted")
    check(
        DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value in partial.data_quality_flags,
        "a partially renamed usage bucket raises provider_schema_drift on the real import path",
    )
    check(
        partial.observation.get("unmapped_usage_fields") == ["input_token_count"],
        "the imported event retains the bounded unknown usage path",
    )
    check(partial_report.provider_schema_drift_events == 1, "import evidence quantifies provider schema drift")

    total_home = work / "total"
    write_session(
        total_home,
        "project-a",
        [assistant_line("total", {"input_token_count": 500, "output_token_count": 100})],
    )
    total = import_new_claude_code_events(claude_home=total_home)[0]
    check(total.event_contributing_tokens == 0, "fully renamed usage contributes zero rather than fabricated tokens")
    check(
        {
            DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value,
            DataQualityFlag.RAW_USAGE_MISSING.value,
        }.issubset(total.data_quality_flags),
        "fully unrecognized imported usage raises both schema drift and raw_usage_missing",
    )

    first_home = work / "location-one"
    second_home = work / "location-two"
    source_line = assistant_line("same-request", {"input_tokens": 12, "output_tokens": 3})
    write_session(first_home, "old-project-name", [source_line])
    write_session(second_home, "renamed-project", [source_line])
    first = import_new_claude_code_events(claude_home=first_home)[0]
    second = import_new_claude_code_events(claude_home=second_home)[0]
    check(first.event_id == second.event_id, "event_id depends on sessionId + requestId, never the absolute path")
    check(
        first.request_correlation_id == second.request_correlation_id,
        "request correlation remains stable after a project or Claude home move",
    )

    copied_home = work / "copied"
    write_session(copied_home, "copy-a", [source_line])
    write_session(copied_home, "copy-b", [source_line])
    copied = import_new_claude_code_events(claude_home=copied_home)
    check(len(copied) == 1, "two copies of one session/request are deduplicated during one import")

    # Existing ledgers contain the old path-derived id. The stable source fields already
    # stored on those events must bridge the migration and prevent a one-time replay.
    legacy_payload = first.to_dict()
    legacy_payload["event_id"] = "legacy-path-derived-id"
    legacy_payload["request_correlation_id"] = "legacy-path-derived-correlation"
    legacy = TokenEvent.from_dict(legacy_payload)
    repository = FileRepository(str(work / "legacy-ledger.jsonl"))
    check(repository.append_unique([legacy]) == [legacy.event_id], "legacy imported event is persisted")
    check(repository.append_unique([second]) == [], "stable replay is rejected against a legacy path-derived event")
    check(len(repository.read_all()) == 1, "path migration cannot inflate an existing Claude ledger")
finally:
    if owned_temp:
        shutil.rmtree(work, ignore_errors=True)

sys.exit(check.report("RESULT test_claude_import_integrity"))
