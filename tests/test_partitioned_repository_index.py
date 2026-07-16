"""Disposable partition index: deduplication, recovery, and concurrency checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402


def event(event_id: str, trace_id: str, timestamp: str) -> TokenEvent:
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"request-{event_id}",
        trace_id=trace_id,
        span_id="span",
        timestamp=timestamp,
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT,
                1,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=1,
        observation={"authoritative": True},
    )


def worker() -> int:
    root, trace_id, result_path = sys.argv[2:]
    appended = PartitionedFileRepository(root).append_unique(
        [event("race", trace_id, "2026-07-12T10:00:00Z")]
    )
    Path(result_path).write_text(json.dumps(appended), encoding="utf-8")
    return 0


if len(sys.argv) > 1 and sys.argv[1] == "worker":
    raise SystemExit(worker())


_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


work = os.path.abspath(f".test_partitioned_repository_index_{uuid.uuid4().hex}")
root = os.path.join(work, "events")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work, exist_ok=True)

repository = PartitionedFileRepository(root)
first = event("shared", "trace-a", "2026-07-10T10:00:00Z")
duplicate_elsewhere = event("shared", "trace-b", "2026-07-11T10:00:00Z")
check(repository.append_unique([first]) == ["shared"], "first event id is persisted")
check(os.path.exists(repository.index_path), "partition index is created beside the JSONL partitions")
check(repository.append_unique([duplicate_elsewhere]) == [], "global duplicate is rejected across partitions")
check([item.event_id for item in repository.iter_events()] == ["shared"], "JSONL stores one canonical event")

# A direct partition write bypasses the index. The next indexed operation detects the
# changed partition signature and repairs the derived index before making a decision.
external = event("external", "trace-external", "2026-07-10T11:00:00Z")
FileRepository(repository._path_for_event(external)).append(external)
external_duplicate = event("external", "trace-other", "2026-07-12T11:00:00Z")
check(
    repository.append_unique([external_duplicate]) == [],
    "out-of-band JSONL append is indexed before deduplication",
)

os.remove(repository.index_path)
rebuilt = PartitionedFileRepository(root)
check(rebuilt.append_unique([duplicate_elsewhere]) == [], "missing index is rebuilt from JSONL")
check(rebuilt.event_ids() == {"shared", "external"}, "rebuilt index contains every source event id")

with open(rebuilt.index_path, "wb") as handle:
    handle.write(b"not a sqlite database")
recovered = PartitionedFileRepository(root)
check(recovered.event_ids() == {"shared", "external"}, "corrupt index is discarded and rebuilt")

race_root = os.path.join(work, "race")
results = [os.path.join(work, f"race-{index}.json") for index in range(2)]
processes = [
    subprocess.Popen([sys.executable, __file__, "worker", race_root, f"trace-{index}", results[index]])
    for index in range(2)
]
check(all(process.wait(timeout=30) == 0 for process in processes), "parallel writers finish cleanly")
race_results = [json.loads(Path(path).read_text(encoding="utf-8")) for path in results]
check(sorted(race_results, key=len) == [[], ["race"]], "parallel append_unique has exactly one winner")
check(
    [item.event_id for item in PartitionedFileRepository(race_root).iter_events()] == ["race"],
    "parallel dedup persists exactly one JSONL event",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if _failures else 0)
