"""Periodic collector monitor task plan must remain bounded and secret-free."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
script = root / "scripts" / "tt-collector-monitor-task.ps1"
task_runner = root / "scripts" / "tt-collector-monitor-task-run.ps1"

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
    ],
    cwd=root,
    env=environment,
    capture_output=True,
    text=True,
    timeout=20,
    check=False,
)
check(result.returncode == 0, "monitor task plan renders without registration")
plan = json.loads(result.stdout)
check(plan["interval_seconds"] == 60, "monitor task uses a one-minute interval")
check(
    plan["triggers"] == ["at_startup", "at_logon", "every_minute"],
    "monitor covers startup, logon, and periodic catch-up",
)
check(plan["start_when_available"] is True, "monitor catches a missed scheduled probe")
check(plan["dont_stop_on_idle_end"] is True, "monitor is not terminated when user activity resumes")
check(plan["collector_task_name"] == "AI Token Tracker Collector", "monitor targets the supervised collector task")
check(plan["recovery_delay_seconds"] == 15, "monitor recovery uses a bounded delay")
check(plan["working_directory"] == r"C:\tracker-test-data", "monitor starts from the operational data directory")
check(plan["health_log"].startswith(r"C:\tracker-test-data"), "health evidence stays beside the configured store")
check(plan["alert_log"].startswith(r"C:\tracker-test-data"), "alert evidence stays beside the configured store")
check(plan["task_log"].endswith("collector-monitor-launcher.log"), "monitor launcher uses a fresh dedicated log")
check("must-not-appear" not in result.stdout, "monitor task plan never serializes the auth token")

script_text = script.read_text(encoding="utf-8")
check("inspection_error" in script_text, "monitor task status fails closed on access errors")
check("-RepetitionInterval (New-TimeSpan -Minutes 1)" in script_text, "monitor task is periodic")
check("New-ScheduledTaskTrigger -AtStartup" in script_text, "monitor has an at-startup trigger")
check("tt-collector-monitor-task-run.ps1" in script_text, "monitor action uses the dedicated PowerShell launcher")
check("TRACKER_AUTH_TOKEN" not in script_text, "monitor task action never embeds an authentication secret")

work = Path(os.getcwd()) / f".test_monitor_task_runner_{uuid.uuid4().hex}"
work.mkdir(parents=True, exist_ok=False)
health_log = work / "collector-health.jsonl"
alert_log = work / "collector-alerts.jsonl"
task_log = work / "collector-monitor-task.log"
runner_environment = dict(environment)
runner_environment["TRACKER_MONITOR_URL"] = "http://127.0.0.1:9"
runner_result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(task_runner),
        "-HealthLog",
        str(health_log),
        "-AlertLog",
        str(alert_log),
        "-TaskLog",
        str(task_log),
        "-CollectorTaskName",
        f"AI Token Tracker Missing Test {uuid.uuid4().hex}",
        "-RecoveryDelaySeconds",
        "1",
    ],
    cwd=work,
    env=runner_environment,
    capture_output=True,
    text=True,
    timeout=20,
    check=False,
)
check(runner_result.returncode == 1, "monitor task launcher preserves an offline probe exit code")
check(health_log.is_file() and alert_log.is_file(), "launcher writes health and alert evidence while offline")
check(task_log.is_file() and "offline" in task_log.read_text(encoding="utf-8-sig"), "launcher writes its task log")
check("collector_recovery_failure" in task_log.read_text(encoding="utf-8-sig"), "failed recovery is explicit and bounded")
shutil.rmtree(work, ignore_errors=True)

sys.exit(check.report("RESULT test_collector_monitor_task_plan"))
