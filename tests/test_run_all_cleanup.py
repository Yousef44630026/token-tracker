"""The all-tests gate isolates scratch data and attributes persistent debris only once."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tests.run_all import (  # noqa: E402
    LINT_PATHS,
    _cleanup_new_test_artifacts,
    _make_test_run_root,
    _remove_path_with_retry,
    _run_lint_gate,
    _run_test_script,
)

check = make_checker()
root = Path(tempfile.mkdtemp(prefix="tracker-runner-cleanup-test-"))
repo_root = Path(os.environ.get("TRACKER_REPO_ROOT", Path(__file__).resolve().parents[1]))

try:
    ordinary = root / ".test_ordinary"
    ordinary.mkdir()
    (ordinary / "payload.txt").write_text("test", encoding="utf-8")
    failures, baseline = _cleanup_new_test_artifacts(root, set(), attempts=1)
    check(failures == [], "ordinary debris is removed without a cleanup failure")
    check(not ordinary.exists() and baseline == set(), "cleanup re-checks disk state before updating the baseline")

    transient = root / ".test_transient_lock"
    transient.mkdir()
    real_rmtree = shutil.rmtree
    calls = [0]

    def flaky_rmtree(path):
        calls[0] += 1
        if calls[0] < 3:
            raise PermissionError("simulated transient handle")
        real_rmtree(path)

    with patch("tests.run_all.shutil.rmtree", side_effect=flaky_rmtree):
        removed = _remove_path_with_retry(transient, attempts=4, initial_delay_seconds=0)
    check(removed and calls[0] == 3, "cleanup retries a transient handle and confirms eventual deletion")

    persistent = root / ".test_persistent_lock"
    persistent.mkdir()
    with patch("tests.run_all.shutil.rmtree", side_effect=PermissionError("simulated persistent handle")):
        first_failures, first_baseline = _cleanup_new_test_artifacts(root, set(), attempts=1)
        second_failures, second_baseline = _cleanup_new_test_artifacts(root, first_baseline, attempts=1)
    check(first_failures == [persistent.name], "a persistent leftover is attributed to the creating test")
    check(second_failures == [], "the same leftover is not re-attributed to every subsequent test")
    check(second_baseline == {persistent.resolve()}, "an unresolved leftover remains in the next attribution baseline")
    shutil.rmtree(persistent)

    configured_parent = root / f"configured-{uuid.uuid4().hex}"
    with patch.dict(os.environ, {"TRACKER_TEST_TMP_ROOT": str(configured_parent)}):
        run_root = _make_test_run_root()
    check(run_root.parent == configured_parent.resolve(), "test run workspaces honor an out-of-repo temp root")
    check("ai-token-tracker-tests-" in run_root.name, "temporary run roots have a recognizable bounded prefix")
    shutil.rmtree(run_root)

    timeout_workspace = root / "timeout-workspace"
    timeout_workspace.mkdir()
    timeout = subprocess.TimeoutExpired([sys.executable, "test_hang.py"], timeout=0.01)
    with patch("tests.run_all.subprocess.run", side_effect=timeout) as mocked_run:
        returncode, timed_out = _run_test_script(
            root / "test_hang.py",
            workspace=timeout_workspace,
            environment={},
            timeout_seconds=0.01,
        )
    check(returncode is None and timed_out, "a hung test is converted into an attributed timeout")
    check(mocked_run.call_args.kwargs["timeout"] == 0.01, "the per-test timeout is enforced by subprocess")

    lint_timeout = subprocess.TimeoutExpired([sys.executable, "-m", "ruff"], timeout=0.01)
    with patch("tests.run_all.subprocess.run", side_effect=lint_timeout) as mocked_lint:
        lint_failures = _run_lint_gate(repo_root, {}, timeout_seconds=0.01)
    check(lint_failures == ["lint:ruff:timeout"], "a hung lint command fails closed with an attributed timeout")
    lint_command = mocked_lint.call_args.args[0]
    check(lint_command[-len(LINT_PATHS) :] == list(LINT_PATHS), "lint scans only the Python source roots")
    check("." not in lint_command[-len(LINT_PATHS) :], "lint does not recursively scan the whole OneDrive repository")

    cmd_wrapper = (repo_root / "scripts" / "tt-check.cmd").read_text(encoding="utf-8").lower()
    check("tests\\run_all.py %*" in cmd_wrapper, "Windows check wrapper delegates to the isolated canonical runner")
    check(
        "tests\\test_operational_doctor.py" not in cmd_wrapper,
        "Windows check wrapper does not maintain a divergent in-repo test manifest",
    )
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_run_all_cleanup"))
