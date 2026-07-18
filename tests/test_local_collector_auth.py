"""Operational tasks must share a generated local bearer without exposing it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

if os.name != "nt":
    print("[SKIP] test_local_collector_auth: Windows ACL and PowerShell setup contract")
    raise SystemExit(0)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import _parser  # noqa: E402
from tests._harness import make_checker  # noqa: E402
from tracker.ops.auth_token import load_auth_token  # noqa: E402
from tracker.ops.doctor import _network_posture_check  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
script = root / "scripts" / "tt-local-auth.ps1"
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or Path.cwd() / f".test_local_auth_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)
token_file = work / "config" / "collector-auth.token"
environment = dict(os.environ)
environment["TRACKER_AUTH_TOKEN_FILE"] = str(token_file)

try:
    configured = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Mode", "Plan"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    check(configured.returncode == 0, "local collector authentication plan renders without writing a secret")
    status = json.loads(configured.stdout)
    check(status["configured"] is False and status["token_file"] == str(token_file), "auth plan targets the external config directory")
    script_text = script.read_text(encoding="utf-8")
    check("RandomNumberGenerator" in script_text, "setup uses a cryptographic random bearer")
    check("SetAccessRuleProtection" in script_text and "Set-Acl" in script_text, "setup restricts token-file ACLs")

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token = "unit-local-auth-token-0123456789abcdef"
    token_file.write_text(token, encoding="utf-8")
    check(token not in configured.stdout, "setup output never reveals a bearer")
    check(load_auth_token({"TRACKER_AUTH_TOKEN_FILE": str(token_file)}) == token, "Python services load the shared token file")
    configured_parser = _parser({"TRACKER_AUTH_TOKEN_FILE": str(token_file)}).parse_args([])
    check(configured_parser.auth_token == token, "collector CLI enables bearer auth from the shared file")
    check(
        _network_posture_check({"TRACKER_AUTH_TOKEN_FILE": str(token_file)}).status == "pass",
        "Doctor passes a loopback collector only when the shared bearer is present",
    )
    check(_network_posture_check({}).status == "warn", "Doctor makes unauthenticated loopback operation visible")

    token_file.write_text("too-short", encoding="utf-8")
    check(
        _network_posture_check({"TRACKER_AUTH_TOKEN_FILE": str(token_file)}).status == "fail",
        "malformed auth files fail closed instead of reopening the collector",
    )
    token_file.write_text(token, encoding="utf-8")

    runner_names = (
        "tt-collector-task-run.ps1",
        "tt-collector-monitor-task-run.ps1",
        "tt-claude-import-task-run.ps1",
        "tt-doctor-watchdog-task-run.ps1",
    )
    for name in runner_names:
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        check("TRACKER_AUTH_TOKEN_FILE" in text, f"{name} passes only the secret-file path")
        check(token not in text, f"{name} never embeds the generated token")

    for name in (
        "tt-collector-task-run.ps1",
        "tt-collector-monitor-task-run.ps1",
        "tt-claude-import-task-run.ps1",
    ):
        text = (root / "scripts" / name).read_text(encoding="utf-8")
        check(
            "if (-not $AuthTokenFile)" in text and '"collector-auth.token"' in text,
            f"{name} keeps legacy scheduled tasks compatible with the external auth file",
        )
finally:
    if owned_temp:
        shutil.rmtree(work, ignore_errors=True)

sys.exit(check.report("RESULT test_local_collector_auth"))
