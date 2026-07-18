"""Scheduled dashboard refresh remains atomic, inspectable, and secret-free."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

if os.name != "nt":
    print("[SKIP] test_dashboard_task_plan: Windows Task Scheduler contract")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
task_script = root / "scripts" / "tt-dashboard-task.ps1"
task_runner = root / "scripts" / "tt-dashboard-task-run.ps1"
environment = dict(os.environ)
environment.update(
    {
        "AI_TOKEN_TRACKER_PYTHON": sys.executable,
        "TRACKER_STORE": r"C:\tracker-test-data\collector.jsonl",
        "TRACKER_AUTH_TOKEN": "must-not-appear",
    }
)

plan_result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(task_script),
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
check(plan_result.returncode == 0, "dashboard task plan renders without registration")
plan = json.loads(plan_result.stdout)
check(plan["triggers"] == ["at_logon", "every_60_minutes"], "dashboard catches logon and refreshes hourly")
check(plan["start_when_available"] is True, "missed dashboard refreshes catch up after sleep or shutdown")
check(plan["dont_stop_on_idle_end"] is True, "dashboard refresh is not stopped at the idle boundary")
check(plan["working_directory"] == r"C:\tracker-test-data", "dashboard task runs beside the operational store")
check(plan["output_file"] == r"C:\tracker-test-data\dashboard.xlsx", "dashboard output stays off the sync volume")
check(plan["prices_configured"] is False, "missing prices remain explicit rather than fabricated")
check("must-not-appear" not in plan_result.stdout, "dashboard plan never serializes the collector auth token")

task_text = task_script.read_text(encoding="utf-8")
runner_text = task_runner.read_text(encoding="utf-8")
check("inspection_error" in task_text, "dashboard task status fails closed on inspection errors")
check("-StartWhenAvailable" in task_text, "installed task catches missed refreshes")
check("-DontStopOnIdleEnd" in task_text, "installed task matches the idle-safe plan")
check("tt-dashboard-task-run.ps1" in task_text, "dashboard task uses a dedicated launcher")
check("TRACKER_AUTH_TOKEN" not in task_text, "dashboard task definition embeds no collector secret")
check("Move-Item -LiteralPath $temporaryOutput" in runner_text, "workbook is published only after generation succeeds")
check("$temporaryEvidence" in runner_text, "refresh evidence is written through a temporary file")

work = Path(os.getcwd()) / f".test_dashboard_task_{uuid.uuid4().hex}"
data_dir = work / "data"
data_dir.mkdir(parents=True, exist_ok=False)
event = TokenEvent(
    event_id="dashboard-task-event",
    request_correlation_id="dashboard-task-request",
    trace_id="dashboard-task-trace",
    span_id="span",
    provider="openai",
    model="test-model",
    quantities=[
        TokenQuantity(
            TokenType.OUTPUT,
            7,
            PrecisionLevel.EXACT,
            UsageSource.PROVIDER_RESPONSE,
            Additivity.TOTAL_CONTRIBUTING,
        )
    ],
    provider_total_tokens=7,
    timestamp="2026-07-16T12:00:00Z",
    observation={"authoritative": True, "status": "complete"},
)
(data_dir / "events.jsonl").write_text(json.dumps(event.to_dict()) + "\n", encoding="utf-8")
task_log = work / "health" / "dashboard-refresh.log"
evidence_file = work / "health" / "dashboard-refresh.json"
output_file = work / "dashboard.xlsx"
run_result = subprocess.run(
    [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(task_runner),
        "-TaskLog",
        str(task_log),
        "-EvidenceFile",
        str(evidence_file),
        "-DataDir",
        str(data_dir),
        "-OutputFile",
        str(output_file),
    ],
    cwd=root,
    env=environment,
    capture_output=True,
    text=True,
    timeout=120,
    check=False,
)
check(run_result.returncode == 0, f"dashboard task runner succeeds ({run_result.stderr.strip()})")
evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
check(evidence["status"] == "ok" and evidence["exit_code"] == 0, "task evidence reports a successful refresh")
report = evidence.get("report") or {}
check(report.get("valid_events") == 1, "task evidence carries the dashboard source count")
check(report.get("skipped_lines") == 0, "task evidence exposes zero skipped source rows")
check(output_file.is_file(), "task publishes the completed workbook at the stable path")
check(not list(work.glob("dashboard-refresh-*.xlsx")), "temporary workbook is removed after publication")
check(task_log.is_file(), "task appends an audit log entry")

shutil.rmtree(work, ignore_errors=True)
sys.exit(check.report("RESULT test_dashboard_task_plan"))
