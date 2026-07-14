"""Periodic collector monitor task plan must remain bounded and secret-free."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
script = root / "scripts" / "tt-collector-monitor-task.ps1"

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
check(plan["health_log"].startswith(r"C:\tracker-test-data"), "health evidence stays beside the configured store")
check(plan["alert_log"].startswith(r"C:\tracker-test-data"), "alert evidence stays beside the configured store")
check("must-not-appear" not in result.stdout, "monitor task plan never serializes the auth token")

script_text = script.read_text(encoding="utf-8")
check("inspection_error" in script_text, "monitor task status fails closed on access errors")
check("-RepetitionInterval (New-TimeSpan -Minutes 1)" in script_text, "monitor task is periodic")
check("TRACKER_AUTH_TOKEN" not in script_text, "monitor task action never embeds an authentication secret")

sys.exit(check.report("RESULT test_collector_monitor_task_plan"))
