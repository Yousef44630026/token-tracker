"""The scheduled Doctor watchdog must persist evidence and alert on unhealthy checks."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.doctor import DoctorCheck  # noqa: E402
from tracker.ops.doctor_watchdog import record_doctor_result  # noqa: E402

check = make_checker()
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or Path.cwd() / f".test_doctor_watchdog_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)
evidence = work / "doctor-watchdog.json"
task_log = work / "doctor-watchdog.jsonl"
alert_log = work / "doctor-alerts.jsonl"
now = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)

try:
    real_replace = os.replace
    replace_calls = [0]

    def transient_replace(source, destination):
        replace_calls[0] += 1
        if replace_calls[0] < 3:
            raise PermissionError("simulated transient Windows handle")
        real_replace(source, destination)

    with patch("tracker.ops.doctor_watchdog.os.replace", side_effect=transient_replace):
        healthy = record_doctor_result(
            [DoctorCheck("collector-health-evidence", "pass", "fresh")],
            evidence_file=evidence,
            task_log=task_log,
            alert_log=alert_log,
            strict_warnings=True,
            now=now,
        )
    check(replace_calls[0] == 3, "atomic evidence publication retries transient Windows handles")
    check(healthy["status"] == "ok" and healthy["exit_code"] == 0, "healthy Doctor run is recorded as ok")
    check(json.loads(evidence.read_text(encoding="utf-8")) == healthy, "latest evidence is atomically readable JSON")
    check(not alert_log.exists(), "healthy run does not create a false alert")

    unhealthy = record_doctor_result(
        [DoctorCheck("claude-import-evidence", "fail", "checkpoint_invalid")],
        evidence_file=evidence,
        task_log=task_log,
        alert_log=alert_log,
        strict_warnings=True,
        now=now + dt.timedelta(hours=1),
    )
    check(unhealthy["status"] == "failed" and unhealthy["exit_code"] == 1, "failed import dead-man makes the watchdog fail")
    check(unhealthy["failed_checks"] == ["claude-import-evidence"], "alert identifies the failed dead-man check")
    alert = json.loads(alert_log.read_text(encoding="utf-8").splitlines()[-1])
    check(alert["failed_checks"] == ["claude-import-evidence"], "failed Doctor run is appended to the alert ledger")

    warning = record_doctor_result(
        [DoctorCheck("storage-substrate", "warn", "sync folder")],
        evidence_file=evidence,
        task_log=task_log,
        alert_log=alert_log,
        strict_warnings=True,
        now=now + dt.timedelta(hours=2),
    )
    check(warning["exit_code"] == 1 and warning["warning_checks"] == ["storage-substrate"], "strict watchdog escalates warnings")
    check(len(task_log.read_text(encoding="utf-8").splitlines()) == 3, "every watchdog run leaves one JSONL audit record")
finally:
    if owned_temp:
        shutil.rmtree(work, ignore_errors=True)

sys.exit(check.report("RESULT test_doctor_watchdog"))
