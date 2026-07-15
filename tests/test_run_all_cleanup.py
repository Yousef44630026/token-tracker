"""The all-tests gate isolates scratch data and attributes persistent debris only once."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tests.run_all import (  # noqa: E402
    _cleanup_new_test_artifacts,
    _make_test_run_root,
    _remove_path_with_retry,
)

check = make_checker()
root = Path(tempfile.mkdtemp(prefix="tracker-runner-cleanup-test-"))

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
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_run_all_cleanup"))
