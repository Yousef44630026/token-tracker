"""Claude import task plan must match its installed behavior and remain secret-free."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

if os.name != "nt":
    print("[SKIP] test_claude_import_task_plan: Windows Task Scheduler contract")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
script = root / "scripts" / "tt-claude-import-task.ps1"
cmd_runner = root / "scripts" / "tt-claude-import.cmd"
task_runner = root / "scripts" / "tt-claude-import-task-run.ps1"
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
check(result.returncode == 0, "Claude import task plan renders without registration")
plan = json.loads(result.stdout)
check(plan["triggers"] == ["at_logon", "every_60_minutes"], "plan reports the triggers actually installed")
check(plan["start_when_available"] is True, "missed import runs catch up")
check(plan["dont_stop_on_idle_end"] is True, "import is not terminated at the idle boundary")
check(plan["working_directory"] == r"C:\tracker-test-data", "task runs beside the operational store")
check(plan["state_file"].endswith("claude-import-state.json"), "task has a durable incremental checkpoint")
check("must-not-appear" not in result.stdout, "task plan never serializes the auth token")

script_text = script.read_text(encoding="utf-8")
check("inspection_error" in script_text, "task status distinguishes inaccessible from not installed")
check("tt-claude-import-task-run.ps1" in script_text, "task uses a dedicated PowerShell launcher")
check("-DontStopOnIdleEnd" in script_text, "installed settings match the idle-safe plan")
check("New-ScheduledTaskTrigger -AtStartup" not in script_text, "non-admin importer does not claim an unusable startup trigger")
check("must-not-appear" not in script_text, "task definition never embeds an authentication secret")
check("TRACKER_AUTH_TOKEN_FILE" in task_runner.read_text(encoding="utf-8"), "task launcher passes only the external secret-file path")
check("%*" in cmd_runner.read_text(encoding="utf-8"), "command runner forwards checkpoint and JSON arguments")
check("--state-file" in task_runner.read_text(encoding="utf-8"), "task launcher passes the checkpoint explicitly")

sys.exit(check.report("RESULT test_claude_import_task_plan"))
