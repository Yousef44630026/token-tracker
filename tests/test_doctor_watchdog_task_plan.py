"""Doctor watchdog task must be periodic, non-admin, bounded, and secret-free."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

if os.name != "nt":
    print("[SKIP] test_doctor_watchdog_task_plan: Windows Task Scheduler contract")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
script = root / "scripts" / "tt-doctor-watchdog-task.ps1"
runner = root / "scripts" / "tt-doctor-watchdog-task-run.ps1"
environment = dict(os.environ)
environment.update(
    {
        "TRACKER_STORE": r"C:\tracker-test-data\collector.jsonl",
        "TRACKER_AUTH_TOKEN": "must-not-appear",
    }
)

result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        "Plan",
        "-IntervalMinutes",
        "60",
    ],
    cwd=root,
    env=environment,
    capture_output=True,
    text=True,
    timeout=20,
    check=False,
)
check(result.returncode == 0, "Doctor watchdog plan renders without task registration")
plan = json.loads(result.stdout)
check(plan["triggers"] == ["at_logon", "every_60_minutes"], "watchdog is periodic and standard-user installable")
check(plan["start_when_available"] is True, "missed watchdog runs catch up after sleep")
check(plan["strict_warnings"] is True, "watchdog consumes warnings as operational failures")
check(plan["evidence_file"].endswith("doctor-watchdog.json"), "watchdog has a latest-state JSON artifact")
check(plan["alert_log"].endswith("doctor-alerts.jsonl"), "watchdog has an append-only alert ledger")
check("must-not-appear" not in result.stdout, "task plan never serializes the bearer token")

script_text = script.read_text(encoding="utf-8")
runner_text = runner.read_text(encoding="utf-8")
check("-RunLevel Limited" in script_text, "watchdog runs without administrator privileges")
check("New-ScheduledTaskTrigger -AtStartup" not in script_text, "watchdog does not claim an unusable pre-logon trigger")
check("tracker.ops.doctor_watchdog" in runner_text, "task runner executes the watchdog module")
check("--strict-warnings" in runner_text, "task runner keeps the strict warning gate enabled")
check("--secret-scan-root" in runner_text, "task runner scans the project rather than the runtime data directory")
check("must-not-appear" not in script_text + runner_text, "watchdog files never embed an authentication secret")

sys.exit(check.report("RESULT test_doctor_watchdog_task_plan"))
