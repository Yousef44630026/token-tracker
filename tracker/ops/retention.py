"""Command-line entrypoint for explicit JSONL retention runs."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from tracker.storage.retention import DEFAULT_MAX_AGE_DAYS, DEFAULT_MAX_STORE_BYTES, RetentionPolicy, run_retention


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate and optionally purge AI Token Tracker JSONL segments")
    parser.add_argument("--store", required=True)
    parser.add_argument("--partitioned-store", action="store_true")
    parser.add_argument("--max-store-bytes", type=int, default=DEFAULT_MAX_STORE_BYTES)
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--no-size-rotation", action="store_true")
    parser.add_argument("--no-age-rotation", action="store_true")
    parser.add_argument("--purge-after-days", type=float)
    parser.add_argument("--enable-purge", action="store_true")
    args = parser.parse_args(argv)
    policy = RetentionPolicy(
        max_store_bytes=None if args.no_size_rotation else args.max_store_bytes,
        max_age_days=None if args.no_age_rotation else args.max_age_days,
        purge_after_days=args.purge_after_days,
        purge_enabled=args.enable_purge,
    )
    report = run_retention(args.store, policy, partitioned=args.partitioned_store)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
