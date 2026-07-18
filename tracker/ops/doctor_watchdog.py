"""Scheduled consumer for operational Doctor checks and their dead-man evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tracker.ops.doctor import DoctorCheck, run_checks


def _timestamp(now: dt.datetime | None) -> str:
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    return current.astimezone(dt.UTC).isoformat()


def _replace_with_retry(source: str, destination: Path, *, attempts: int = 6) -> None:
    """Tolerate short Windows scanner/sync handles without weakening atomic publication."""
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(min(0.05 * (2**attempt), 1.0))


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def record_doctor_result(
    checks: Sequence[DoctorCheck],
    *,
    evidence_file: str | os.PathLike[str],
    task_log: str | os.PathLike[str],
    alert_log: str | os.PathLike[str],
    strict_warnings: bool,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Persist one Doctor result and append an alert when the operational gate is red."""
    failures = [item.name for item in checks if item.failed]
    warnings = [item.name for item in checks if item.warned]
    exit_code = 1 if failures or strict_warnings and warnings else 0
    payload: dict[str, Any] = {
        "timestamp": _timestamp(now),
        "status": "ok" if exit_code == 0 else "failed",
        "exit_code": exit_code,
        "strict_warnings": strict_warnings,
        "check_count": len(checks),
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "failed_checks": failures,
        "warning_checks": warnings,
        "checks": [asdict(item) for item in checks],
    }
    _atomic_write_json(Path(evidence_file), payload)
    _append_jsonl(Path(task_log), payload)
    if exit_code:
        _append_jsonl(Path(alert_log), payload)
    return payload


def run_watchdog(
    *,
    store: str,
    evidence_file: str,
    task_log: str,
    alert_log: str,
    strict_warnings: bool = True,
    partitioned_store: bool = False,
    secret_scan_root: str | None = None,
) -> dict[str, Any]:
    """Run the operational Doctor and persist its audit result."""
    checks = run_checks(
        store=store,
        partitioned_store=partitioned_store,
        secret_scan_root=secret_scan_root,
    )
    return record_doctor_result(
        checks,
        evidence_file=evidence_file,
        task_log=task_log,
        alert_log=alert_log,
        strict_warnings=strict_warnings,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run and persist the AI Token Tracker operational Doctor")
    default_store = os.environ.get("TRACKER_STORE") or (
        r"C:\ai-token-tracker-data\collector_events.jsonl" if os.name == "nt" else "collector_events.jsonl"
    )
    runtime = os.path.dirname(os.path.abspath(os.path.expanduser(default_store)))
    health = os.path.join(runtime, "health")
    parser.add_argument("--store", default=default_store)
    parser.add_argument("--partitioned-store", action="store_true")
    parser.add_argument("--evidence-file", default=os.path.join(health, "doctor-watchdog.json"))
    parser.add_argument("--task-log", default=os.path.join(health, "doctor-watchdog.jsonl"))
    parser.add_argument("--alert-log", default=os.path.join(health, "doctor-alerts.jsonl"))
    parser.add_argument("--secret-scan-root", default=os.getcwd())
    parser.add_argument("--strict-warnings", action="store_true")
    args = parser.parse_args(argv)
    result = run_watchdog(
        store=args.store,
        partitioned_store=args.partitioned_store,
        evidence_file=args.evidence_file,
        task_log=args.task_log,
        alert_log=args.alert_log,
        strict_warnings=args.strict_warnings,
        secret_scan_root=args.secret_scan_root,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
