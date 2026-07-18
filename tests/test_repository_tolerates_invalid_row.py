"""A schema-invalid (but JSON-valid) row must not make the whole store unreadable.

Run: python tests/test_repository_tolerates_invalid_row.py

Historical JSONL can contain a row written by an older schema (e.g. a legacy 'status' value
that no longer validates). One such row must not crash read_all / iter_events and take every
other event down with it. The reader skips the bad row and surfaces a COUNT of what it skipped
(INV-6 spirit: a lost record is a visible count, never a silent disappearance).
"""

import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def good_row(eid):
    return {
        "event_id": eid,
        "request_correlation_id": "r",
        "trace_id": "t",
        "span_id": "s",
        "quantities": [
            {
                "token_type": "output",
                "quantity": 7,
                "precision_level": "exact",
                "usage_source": "provider_response",
                "additivity": "total_contributing",
            }
        ],
        "provider_total_tokens": 7,
    }


def legacy_bad_status_row(eid):
    # A row an OLD codex logger wrote: a usage-source string landed in observation.status,
    # which no longer validates. JSON-valid, schema-invalid.
    row = good_row(eid)
    row["observation"] = {"authoritative": True, "status": "codex_local_token_count"}
    return row


root = os.path.abspath(f".test_repository_tolerates_invalid_row_{uuid.uuid4().hex}")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root, exist_ok=True)

try:
    path = os.path.join(root, "events.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(good_row("good-1")) + "\n")
        fh.write(json.dumps(legacy_bad_status_row("legacy-bad")) + "\n")
        fh.write(json.dumps(good_row("good-2")) + "\n")

    repo = FileRepository(path)

    # read_all must NOT raise, and must return the two good events.
    try:
        events = repo.read_all()
        raised = False
    except Exception as exc:  # noqa: BLE001
        raised = True
        events = []
        print("  unexpected:", type(exc).__name__, exc)
    check(not raised, "read_all does not crash on a schema-invalid row")
    check([e.event_id for e in events] == ["good-1", "good-2"], "the two good events are returned, bad one skipped")

    # the skip is observable, not silent: a count is exposed.
    check(repo.skipped_invalid_count == 1, "repo surfaces skipped_invalid_count == 1")

    # strict mode is still available for callers that want a hard failure.
    strict = FileRepository(path, skip_invalid_records=False)
    try:
        strict.read_all()
        strict_raised = False
    except ValueError:
        strict_raised = True
    check(strict_raised, "skip_invalid_records=False still raises on a bad row (strict opt-out)")

    malformed_path = os.path.join(root, "malformed.jsonl")
    with open(malformed_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(good_row("good-json-1")) + "\n")
        fh.write("{not-json}\n")
        fh.write(json.dumps(good_row("good-json-2")) + "\n")

    malformed_repo = FileRepository(malformed_path)
    malformed_events = malformed_repo.read_all()
    check(
        [e.event_id for e in malformed_events] == ["good-json-1", "good-json-2"],
        "malformed complete JSON row is skipped by default while good rows survive",
    )
    check(malformed_repo.skipped_invalid_count == 1, "malformed complete JSON row increments skipped_invalid_count")

    malformed_strict = FileRepository(malformed_path, skip_invalid_records=False)
    try:
        malformed_strict.read_all()
        malformed_strict_raised = False
    except ValueError:
        malformed_strict_raised = True
    check(malformed_strict_raised, "strict mode still raises on malformed complete JSON")

    partitioned = PartitionedFileRepository(os.path.join(root, "partitioned"))
    first = TokenEvent.from_dict(good_row("same-id"))
    second_row = good_row("same-id")
    second_row["trace_id"] = "other-trace"
    second_row["timestamp"] = "2026-01-02T00:00:00Z"
    second = TokenEvent.from_dict(second_row)
    appended_first = partitioned.append_unique([first])
    appended_second = partitioned.append_unique([second])
    check(appended_first == ["same-id"], "partitioned append_unique stores first event id")
    check(appended_second == [], "partitioned append_unique rejects duplicate id across partitions")
    check(len(partitioned.read_all()) == 1, "partitioned repository remains globally idempotent by event_id")
finally:
    shutil.rmtree(root, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
