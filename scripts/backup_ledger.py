"""Verified, archive-inclusive, off-store backup of the collector ledger.

The active JSONL is usually EMPTY after archive-first rotation — every real event lives in
`<store>.archive/*.jsonl.gz`. A backup that copies only the active file would silently save
nothing. This snapshots the FULL logical ledger (active + every archive segment) via
FileRepository.write_compacted, which holds the store lock (no torn read) and is archive-aware,
then gzips and VERIFIES the backup by reading it back and comparing event count and contributing
total to the source. A manifest records counts and SHA-256 so a restore can be checked.

Restore: `gunzip` the segment (or point a FileRepository at it) — it is a complete JSONL ledger.

Usage:
  python scripts/backup_ledger.py --dest D:\\backups\\ai-token-tracker [--keep 14]
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.derive.derived_fields import event_contributing_tokens  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_and_total(repo: FileRepository) -> tuple[int, int]:
    count = total = 0
    for event in repo.iter_events():
        count += 1
        total += event_contributing_tokens(event)
    return count, total


def main() -> int:
    parser = argparse.ArgumentParser()
    default_store = os.environ.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl")
    default_dest = os.environ.get("TRACKER_BACKUP_DIR", str(Path(default_store).expanduser().resolve().parent / "backups"))
    parser.add_argument("--source", default=default_store)
    parser.add_argument("--dest", default=default_dest)
    parser.add_argument("--keep", type=int, default=14, help="retain this many most-recent backups")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    src_repo = FileRepository(str(source))
    src_count, src_total = _count_and_total(src_repo)

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    final = dest / f"ledger-{stamp}.jsonl.gz"
    manifest_path = dest / f"ledger-{stamp}.manifest.json"

    # 1) lock-consistent, archive-aware logical snapshot into a temp plain JSONL
    tmp_dir = Path(tempfile.mkdtemp(prefix="ledger-backup-"))
    try:
        plain = tmp_dir / "snapshot.jsonl"
        kept = src_repo.write_compacted(str(plain), drop_superseded=False)
        # 2) gzip it
        with open(plain, "rb") as fin, gzip.open(final, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 3) VERIFY: read the backup back and compare to the source
    verify_dir = Path(tempfile.mkdtemp(prefix="ledger-verify-"))
    try:
        verify_jsonl = verify_dir / "collector_events.jsonl"
        with gzip.open(final, "rb") as fin, open(verify_jsonl, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        v_count, v_total = _count_and_total(FileRepository(str(verify_jsonl)))
    finally:
        shutil.rmtree(verify_dir, ignore_errors=True)

    verified = (v_count == src_count == kept) and (v_total == src_total)
    manifest = {
        "created_utc": stamp,
        "source": str(source),
        "backup": str(final),
        "source_events": src_count,
        "backup_events": v_count,
        "source_contributing_total": src_total,
        "backup_contributing_total": v_total,
        "sha256": _sha256(final),
        "verified": verified,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if not verified:
        # A failed verification must not masquerade as a good backup.
        final.rename(final.with_suffix(".gz.UNVERIFIED"))
        manifest["status"] = "UNVERIFIED_BACKUP_QUARANTINED"

    # 4) prune old verified backups (keep the newest N pairs)
    backups = sorted(dest.glob("ledger-*.jsonl.gz"))
    for old in backups[: max(0, len(backups) - args.keep)]:
        old.unlink(missing_ok=True)
        old.with_name(old.name.replace(".jsonl.gz", ".manifest.json")).unlink(missing_ok=True)

    if args.json:
        print(json.dumps(manifest))
    else:
        print(f"Ledger backup {'VERIFIED' if verified else 'FAILED VERIFICATION'}")
        print(f"  source : {src_count} events, total {src_total}")
        print(f"  backup : {v_count} events, total {v_total} -> {final if verified else final.with_suffix('.gz.UNVERIFIED')}")
        print(f"  sha256 : {manifest['sha256'][:16]}")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
