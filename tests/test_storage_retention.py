"""Falsifiers for explicit, archive-first JSONL retention.

Run: scripts/_python.cmd tests/test_storage_retention.py
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from urllib import request as url_request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.ops.doctor import _retention_check  # noqa: E402
from tracker.ops.retention import main as retention_main  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402
from tracker.storage.retention import RetentionPolicy, inspect_retention, run_retention  # noqa: E402

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def event(index: int, timestamp: str) -> TokenEvent:
    return TokenEvent(
        event_id=f"retention-{index}",
        request_correlation_id=f"request-{index}",
        trace_id=f"trace-{index % 2}",
        span_id=f"span-{index}",
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT,
                index,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=index,
        timestamp=timestamp,
        observation={"authoritative": True, "status": "complete"},
    )


def total(events: list[TokenEvent]) -> int:
    return sum(item.event_contributing_tokens for item in events)


def stats(base: str) -> dict[str, int]:
    with url_request.urlopen(base + "/v1/stats?summary=1", timeout=5) as response:
        return json.loads(response.read())


root = Path(f".test_storage_retention_{uuid.uuid4().hex}").resolve()
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True)
now = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)

try:
    flat_path = root / "flat.jsonl"
    flat = FileRepository(str(flat_path))
    original = [event(10, "2026-06-01T00:00:00Z"), event(20, "2026-06-02T00:00:00Z")]
    flat.append_many(original)
    before_events = flat.read_all()

    server = create_server(flat, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        before_stats = stats(base)
        report = run_retention(
            str(flat_path),
            RetentionPolicy(max_store_bytes=1, max_age_days=None),
            now=now,
        )
        after_stats = stats(base)
    finally:
        server.shutdown()
        server.server_close()

    after_events = flat.read_all()
    check(report.rotated_segment_count == 1, "size threshold rotates one complete flat segment")
    check(flat_path.stat().st_size == 0, "rotation leaves an empty appendable active JSONL")
    check([item.event_id for item in after_events] == [item.event_id for item in before_events], "no event is lost across rotation")
    check(total(after_events) == total(before_events), "totals before and after rotation are identical")
    check(before_stats["total"] == after_stats["total"] == 30, "collector stats include rotated segments")
    check(report.purged_segment_count == 0, "default retention archives and never purges")
    check(flat.append_unique([original[0]]) == [], "archived event ids remain idempotent")

    archive = next(Path(flat.archive_dir).glob("*.jsonl.gz"))
    archive_mtime = (now - dt.timedelta(days=10)).timestamp()
    os.utime(archive, (archive_mtime, archive_mtime))
    no_purge = run_retention(
        str(flat_path),
        RetentionPolicy(
            max_store_bytes=None,
            max_age_days=None,
            purge_after_days=1,
            purge_enabled=False,
        ),
        now=now,
    )
    check(no_purge.purged_segment_count == 0 and archive.exists(), "purge age alone cannot delete an archive")

    # Simulate the safe crash window: archive publication succeeded but active replacement did not.
    crash_path = root / "crash.jsonl"
    crash_repo = FileRepository(str(crash_path))
    crash_repo.append(event(7, "2026-06-03T00:00:00Z"))
    Path(crash_repo.archive_dir).mkdir(parents=True)
    with gzip.open(Path(crash_repo.archive_dir) / "published-before-crash.jsonl.gz", "wb") as handle:
        handle.write(crash_path.read_bytes())
    check(len(crash_repo.read_all()) == 1, "archive-first crash overlap cannot double count an event")

    partition_root = root / "partitioned"
    partitioned = PartitionedFileRepository(str(partition_root))
    partitioned.append_many(original)
    partition_before = partitioned.read_all()
    partition_report = run_retention(
        str(partition_root),
        RetentionPolicy(max_store_bytes=1, max_age_days=None),
        partitioned=True,
        now=now,
    )
    partition_after = partitioned.read_all()
    check(partition_report.rotated_segment_count == 2, "partitioned retention rotates each eligible partition")
    check(
        {item.event_id for item in partition_after} == {item.event_id for item in partition_before},
        "partition index rebuild sees archived events",
    )
    check(total(partition_after) == total(partition_before), "partitioned totals survive rotation")
    check(partitioned.append_unique([original[0]]) == [], "partitioned archived ids stay deduplicated")

    status = inspect_retention(str(flat_path), now=now)
    check(status.retention_has_run and status.segment_count >= 1, "retention inspection reports prior execution and segments")
    doctor = _retention_check(str(flat_path), partitioned=False, max_store_bytes=10_000_000, max_age_days=1, now=now)
    check(doctor.name == "storage-retention" and doctor.status == "pass", "Doctor reports a healthy retention check")
    check(
        status.oldest_event_age_days is not None
        and status.oldest_event_age_days > 1
        and status.oldest_active_event_age_days is None,
        "archived history remains visible without falsely breaching the active rotation age",
    )

    state = json.loads(Path(f"{flat_path}.retention.json").read_text(encoding="utf-8"))
    forbidden = {"event_contributing_tokens", "observed_total_contributing_tokens", "provider_total_tokens"}
    check(forbidden.isdisjoint(state), "retention state stores operations, never accounting totals")

    cli_path = root / "cli.jsonl"
    cli_repo = FileRepository(str(cli_path))
    cli_repo.append(event(11, "2026-06-04T00:00:00Z"))
    cli_exit = retention_main(
        ["--store", str(cli_path), "--max-store-bytes", "1", "--no-age-rotation"]
    )
    check(cli_exit == 0 and len(cli_repo.read_all()) == 1, "retention CLI rotates explicitly without data loss")
finally:
    shutil.rmtree(root, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
raise SystemExit(1 if _failures else 0)
