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
check(plan["trigger"] == "at_logon", "supervision plan starts at user logon")
check(plan["restart_count"] > 0, "supervision plan restarts after failures")
check("must-not-appear" not in result.stdout, "supervision plan never serializes the auth token")

runner_text = runner.read_text(encoding="utf-8")
check("-m api.main" in runner_text, "runner starts the supported collector entry point")
check("TRACKER_DURABLE=true" in runner_text, "runner explicitly defaults to durable writes")
check("TRACKER_AUTH_TOKEN" not in runner_text, "runner never embeds an authentication secret")

sys.exit(check.report("RESULT test_collector_task_plan"))
