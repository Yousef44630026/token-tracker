"""Retention and recovery drill for the collector ledger.

Exercises backup, rotation/compaction, simulated loss + restore, and duplicate-recovery
WITHOUT ever mutating the live ledger. The live store is only read, through a
lock-consistent snapshot (FileRepository.write_compacted holds the store lock while it
copies), so a concurrently-writing collector cannot produce a torn snapshot.

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

from tracker.storage.file_repository import FileRepository  # noqa: E402


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count(path: str) -> int:
    return sum(1 for _ in FileRepository(path).iter_events())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=r"C:\ai-token-tracker-data\collector_events.jsonl")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    source_parent = os.path.dirname(args.source)
    drill_parent = source_parent if os.path.isdir(source_parent) else None
    drill_dir = tempfile.mkdtemp(prefix="recovery-drill-", dir=drill_parent)
    try:
        snapshot = os.path.join(drill_dir, "snapshot.jsonl")
        backup = os.path.join(drill_dir, "backup.jsonl")
        rotated = os.path.join(drill_dir, "rotated.jsonl")
        primary = os.path.join(drill_dir, "primary.jsonl")

        # 1. Lock-consistent snapshot of the LIVE ledger (drop_superseded=False = faithful copy).
        snap_count = FileRepository(args.source).write_compacted(snapshot, drop_superseded=False)
        base_sha = _sha256(snapshot)
        check("snapshot", snap_count > 0, f"{snap_count} events, sha {base_sha[:12]}")

        # 2. BACKUP: byte-for-byte copy; hashes must match.
        shutil.copy2(snapshot, backup)
        check("backup_integrity", _sha256(backup) == base_sha, "backup sha == snapshot sha")

        # 3. ROTATION / COMPACTION: produce a compacted copy (drops superseded); must be readable.
        rotated_kept = FileRepository(snapshot).write_compacted(rotated, drop_superseded=True)
        rotated_readback = _count(rotated)
        check("rotation_compaction", rotated_kept == rotated_readback, f"kept {rotated_kept}, read back {rotated_readback}")

        # 4. SIMULATED LOSS then RESTORE from backup; restored store must match the baseline.
        shutil.copy2(snapshot, primary)
        os.remove(primary)
        check("simulated_loss", not os.path.exists(primary), "primary deleted")
        shutil.copy2(backup, primary)
        restored_sha = _sha256(primary)
        restored_count = _count(primary)
        check("restore_integrity", restored_sha == base_sha and restored_count == snap_count,
              f"restored {restored_count} events, sha match={restored_sha == base_sha}")

        # 5. DUPLICATE-RECOVERY: re-append every restored event; the store must persist ZERO
        #    duplicates and stay byte-identical (dedup by deterministic event_id).
        repo = FileRepository(primary)
        events = repo.read_all()
        newly = repo.append_unique(events)
        after_sha = _sha256(primary)
        after_count = _count(primary)
        check("duplicate_recovery", newly == [] and after_count == snap_count and after_sha == base_sha,
              f"re-appended {len(events)}, newly persisted {len(newly)}, count {after_count}, sha unchanged={after_sha == base_sha}")

        # 6. READABILITY: full streaming read of the restored store.
        check("readability", _count(primary) == snap_count, f"streamed {snap_count} events")

        passed = all(ok for _, ok, _ in checks)
        summary = {
            "source": args.source,
            "snapshot_events": snap_count,
            "baseline_sha256": base_sha,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
            "passed": passed,
        }
        if args.json:
            print(json.dumps(summary))
        else:
            print(f"Recovery drill on {args.source}")
            print(f"  live ledger snapshot: {snap_count} events, sha {base_sha[:16]}")
            for name, ok, detail in checks:
                print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
            print(f"RESULT: {'PASS' if passed else 'FAIL'}")
        return 0 if passed else 1
    finally:
        shutil.rmtree(drill_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
