"""Falsify cross-platform wrapper and test-runner drift.

Run: python tests/test_posix_operability.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parents[1]
scripts = {
    "tt-collector-run.sh": "-m api.main",
    "tt-doctor.sh": "-m tracker.ops.doctor",
    "tt-verify.sh": "tracker.proxy.cli provider-matrix",
    "tt-report.sh": "tracker.proxy.cli report",
    "tt-powerbi-export.sh": "tracker.proxy.cli powerbi-export",
}

for name, required_command in scripts.items():
    path = root / "scripts" / name
    body = path.read_text(encoding="utf-8")
    check(body.startswith("#!/usr/bin/env sh\n"), f"{name} declares a POSIX sh interpreter")
    check("AI_TOKEN_TRACKER_PYTHON" in body, f"{name} supports an explicit portable interpreter")
    check(required_command in body, f"{name} invokes the supported Python entry point")

collector = (root / "scripts" / "tt-collector-run.sh").read_text(encoding="utf-8")
check("trap stop_collector" in collector, "POSIX collector forwards shutdown to its managed child")
check("while :" in collector and "TRACKER_RESTART_DELAY_SECONDS" in collector, "POSIX collector supervises bounded restarts")

windows_contracts = (
    "test_claude_import_task_plan.py",
    "test_collector_monitor_task_plan.py",
    "test_collector_task_plan.py",
    "test_dashboard_task_plan.py",
    "test_doctor_watchdog_task_plan.py",
    "test_local_collector_auth.py",
)
for name in windows_contracts:
    body = (root / "tests" / name).read_text(encoding="utf-8")
    check('os.name != "nt"' in body and "[SKIP]" in body, f"{name} skips its Windows-only contract explicitly")

test_text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "tests").rglob("*.py"))
legacy_interpreter = "python" + "-portable"
legacy_home = "C:" + "\\Users\\" + "yerabhaoui"
check(legacy_interpreter not in test_text, "test docstrings contain no user-specific portable-Python path")
check(legacy_home not in test_text, "tests contain no user-specific Windows home path")

posix_doc = (root / "docs" / "POSIX_OPERATIONS.md").read_text(encoding="utf-8")
check("## Cron Equivalent" in posix_doc and "@reboot" in posix_doc, "POSIX docs provide a cron startup equivalent")
check("flock -n" in posix_doc, "periodic cron examples prevent overlapping runs")

if os.name != "nt":
    shell = shutil.which("sh")
    check(shell is not None, "POSIX test host provides sh")
    if shell is not None:
        for name in scripts:
            result = subprocess.run([shell, "-n", str(root / "scripts" / name)], capture_output=True, text=True, check=False)
            check(result.returncode == 0, f"{name} passes sh syntax validation")
else:
    check(True, "sh syntax execution is delegated to the Ubuntu CI matrix leg")

sys.exit(check.report("RESULT test_posix_operability"))
