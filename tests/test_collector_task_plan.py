"""Windows collector supervision plan must be safe and secret-free."""

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
script = root / "scripts" / "tt-collector-task.ps1"
runner = root / "scripts" / "tt-collector-run.cmd"

environment = dict(os.environ)
environment.update(
    {
        "TRACKER_STORE": r"C:\tracker-test-data\collector.jsonl",
        "TRACKER_HOST": "127.0.0.1",
        "TRACKER_PORT": "9876",
        "TRACKER_DURABLE": "true",
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
check(result.returncode == 0, "collector task plan renders without registration")
plan = json.loads(result.stdout)
check(plan["host"] == "127.0.0.1", "supervised collector stays on loopback")
check(plan["port"] == 9876, "supervision plan honors TRACKER_PORT")
check(plan["store"] == r"C:\tracker-test-data\collector.jsonl", "supervision plan honors TRACKER_STORE")
check(plan["durable"] is True, "supervision plan keeps durable persistence enabled")
check(plan["triggers"] == ["at_startup", "at_logon"], "supervision plan covers startup and user logon")
check(plan["start_when_available"] is True, "supervision catches a missed startup trigger")
check(plan["working_directory"] == r"C:\tracker-test-data", "task starts from the non-synced runtime directory")
check(plan["restart_count"] > 0, "supervision plan restarts after failures")
check(plan["process_restart_delay_seconds"] == 10, "runner has a bounded child-process restart delay")
check("must-not-appear" not in result.stdout, "supervision plan never serializes the auth token")

runner_text = runner.read_text(encoding="utf-8")
check("-m api.main" in runner_text, "runner starts the supported collector entry point")
check("TRACKER_DURABLE=true" in runner_text, "runner explicitly defaults to durable writes")
check(":supervise" in runner_text and "goto supervise" in runner_text, "runner restarts a failed collector child")
check("TRACKER_AUTH_TOKEN" not in runner_text, "runner never embeds an authentication secret")

task_script_text = script.read_text(encoding="utf-8")
check("inspection_error" in task_script_text, "status distinguishes inaccessible state from not installed")
check("Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop" in task_script_text, "task inspection fails closed")
check("New-ScheduledTaskTrigger -AtStartup" in task_script_text, "collector has an at-startup trigger")
check("tt-collector-task-run.ps1" in task_script_text, "collector action uses the dedicated PowerShell launcher")

sys.exit(check.report("RESULT test_collector_task_plan"))
