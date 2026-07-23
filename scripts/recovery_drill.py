"""Retention and recovery drill for the collector ledger.

Exercises backup, rotation/compaction, simulated loss + restore, and duplicate-recovery
WITHOUT ever mutating the live ledger. The live store is read in strict mode through a
lock-consistent logical snapshot (FileRepository.write_compacted holds the store lock while
it copies). Any malformed, truncated, or schema-invalid source row fails the drill instead
of being omitted from a false-green backup.

Usage:
  python scripts/recovery_drill.py --source C:\\ai-token-tracker-data\\collector_events.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.derive.effective_events import effective_events  # noqa: E402
from tracker.ops.runtime_fingerprint import runtime_fingerprint  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402
from tracker.storage.retention import RetentionPolicy, inspect_retention, run_retention  # noqa: E402


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(path: str) -> int:
    return sum(1 for _ in _strict_repository(path).iter_events())


def _strict_repository(path: str) -> FileRepository:
    return FileRepository(
        path,
        recover_truncated_tail=False,
        skip_invalid_records=False,
    )


def _accounting_identity(path: str) -> tuple[tuple[str, ...], int]:
    events = list(_strict_repository(path).iter_events())
    event_ids = tuple(sorted(event.event_id for event in events))
    canonical_total = sum(event.event_contributing_tokens for event in effective_events(events))
    return event_ids, canonical_total


def _summary(
    source: str,
    checks: list[tuple[str, bool, str]],
    *,
    snapshot_events: int,
    baseline_sha256: str | None,
) -> dict[str, object]:
    return {
        "evidence_type": "recovery_drill",
        "runtime_fingerprint": runtime_fingerprint(),
        "source": source,
        "snapshot_events": snapshot_events,
        "baseline_sha256": baseline_sha256,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checks": [{"name": name, "passed": ok, "detail": detail} for name, ok, detail in checks],
        "passed": all(ok for _, ok, _ in checks),
    }


def _print_summary(summary: dict[str, object], *, as_json: bool, evidence_output: str | None = None) -> None:
    if evidence_output:
        target = os.path.abspath(evidence_output)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = f"{target}.tmp-{os.getpid()}"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    if as_json:
        print(json.dumps(summary))
        return
    print(f"Recovery drill on {summary['source']}")
    print(
        "  live ledger snapshot: "
        f"{summary['snapshot_events']} events, sha "
        f"{str(summary['baseline_sha256'] or 'unavailable')[:16]}"
    )
    for item in summary["checks"]:
        assert isinstance(item, dict)
        print(f"  [{'PASS' if item['passed'] else 'FAIL'}] {item['name']}: {item['detail']}")
    print(f"RESULT: {'PASS' if summary['passed'] else 'FAIL'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=r"C:\ai-token-tracker-data\collector_events.jsonl")
    parser.add_argument("--work-dir", default=None, help="exact disposable drill directory (primarily for CI)")
    parser.add_argument("--evidence-output", help="atomically publish the machine-readable drill result")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    if args.work_dir:
        drill_dir = os.path.abspath(args.work_dir)
        if os.path.exists(drill_dir):
            raise FileExistsError(f"work directory already exists: {drill_dir}")
        os.makedirs(drill_dir)
    else:
        source_parent = os.path.dirname(args.source)
        drill_parent = source_parent if os.path.isdir(source_parent) else None
        drill_dir = tempfile.mkdtemp(prefix="recovery-drill-", dir=drill_parent)
    try:
        snapshot = os.path.join(drill_dir, "snapshot.jsonl")
        backup = os.path.join(drill_dir, "backup.jsonl")
        retention_copy = os.path.join(drill_dir, "retention.jsonl")
        rotated = os.path.join(drill_dir, "rotated.jsonl")
        primary = os.path.join(drill_dir, "primary.jsonl")

        # 1. Strict, lock-consistent LOGICAL snapshot. This canonicalizes serialization but
        #    retains every valid event, including superseded rows. Corruption must fail here.
        try:
            snap_count = _strict_repository(args.source).write_compacted(
                snapshot,
                drop_superseded=False,
            )
        except (OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            check("source_validation", False, f"{type(exc).__name__}: {exc}")
            summary = _summary(
                args.source,
                checks,
                snapshot_events=0,
                baseline_sha256=None,
            )
            _print_summary(summary, as_json=args.json, evidence_output=args.evidence_output)
            return 1

        base_sha = _sha256(snapshot)
        check("source_validation", True, "strict read accepted every source row")
        check("snapshot", snap_count > 0, f"{snap_count} events, sha {base_sha[:12]}")

        # 2. BACKUP: byte-for-byte copy; hashes must match.
        shutil.copy2(snapshot, backup)
        check("backup_integrity", _sha256(backup) == base_sha, "backup sha == snapshot sha")

        # 3. ARCHIVE-FIRST RETENTION: rotate a disposable byte copy. The source snapshot and
        #    live ledger remain untouched; source identity and canonical accounting must match.
        shutil.copy2(snapshot, retention_copy)
        before_ids, before_total = _accounting_identity(retention_copy)
        retention_report = run_retention(
            retention_copy,
            RetentionPolicy(max_store_bytes=1, max_age_days=None),
        )
        after_ids, after_total = _accounting_identity(retention_copy)
        retention_status = inspect_retention(retention_copy)
        archive_rotation_ok = (
            retention_report.rotated_segment_count == 1
            and retention_report.purged_segment_count == 0
            and os.path.getsize(retention_copy) == 0
            and before_ids == after_ids
            and before_total == after_total
            and retention_status.retention_has_run
            and retention_status.archive_segment_count == 1
        )
        check(
            "archive_first_retention",
            archive_rotation_ok,
            f"events {len(before_ids)} -> {len(after_ids)}, canonical total {before_total} -> {after_total}, purge 0",
        )

        # 4. ROTATION / COMPACTION: produce a compacted copy (drops superseded); must be readable.
        rotated_kept = _strict_repository(snapshot).write_compacted(rotated, drop_superseded=True)
        rotated_readback = _count(rotated)
        check("rotation_compaction", rotated_kept == rotated_readback, f"kept {rotated_kept}, read back {rotated_readback}")

        # 5. SIMULATED LOSS then RESTORE from backup; restored store must match the baseline.
        shutil.copy2(snapshot, primary)
        os.remove(primary)
        check("simulated_loss", not os.path.exists(primary), "primary deleted")
        shutil.copy2(backup, primary)
        restored_sha = _sha256(primary)
        restored_count = _count(primary)
        check("restore_integrity", restored_sha == base_sha and restored_count == snap_count,
              f"restored {restored_count} events, sha match={restored_sha == base_sha}")

        # 6. DUPLICATE-RECOVERY: re-append every restored event; the store must persist ZERO
        #    duplicates and stay byte-identical (dedup by deterministic event_id).
        repo = _strict_repository(primary)
        events = repo.read_all()
        newly = repo.append_unique(events)
        after_sha = _sha256(primary)
        after_count = _count(primary)
        check("duplicate_recovery", newly == [] and after_count == snap_count and after_sha == base_sha,
              f"re-appended {len(events)}, newly persisted {len(newly)}, count {after_count}, sha unchanged={after_sha == base_sha}")

        # 7. READABILITY: full streaming read of the restored store.
        check("readability", _count(primary) == snap_count, f"streamed {snap_count} events")

        summary = _summary(
            args.source,
            checks,
            snapshot_events=snap_count,
            baseline_sha256=base_sha,
        )
        _print_summary(summary, as_json=args.json, evidence_output=args.evidence_output)
        return 0 if summary["passed"] else 1
    finally:
        shutil.rmtree(drill_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
