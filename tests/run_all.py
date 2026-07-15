"""Run every non-live test script with the current Python interpreter."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLEANUP_ATTEMPTS = 8
CLEANUP_INITIAL_DELAY_SECONDS = 0.05
TEST_WORKSPACE_PREFIX = "t"


def _run_lint_gate(repo_root: Path, environment: dict) -> list[str]:
    """Run Ruff over the repository and return failure labels.

    A missing Ruff installation is reported as a visible skip instead of a silent pass.
    """
    failures: list[str] = []
    for label, cmd in (("ruff", [sys.executable, "-m", "ruff", "check", "."]),):
        print(f"\n=== lint: {label} ===")
        try:
            result = subprocess.run(cmd, cwd=repo_root, env=environment, check=False)
        except FileNotFoundError:
            print(f"[SKIP] {label} could not be launched (interpreter/module missing)")
            continue
        if result.returncode == 0:
            continue
        if result.returncode != 0 and _module_missing(sys.executable, cmd[2], environment):
            print(f"[SKIP] {label} not installed in this interpreter — not enforced this run.")
            print(f'       & "{sys.executable}" -m pip install {cmd[2]}')
            continue
        failures.append(f"lint:{label}")
    return failures


def _module_missing(python: str, module: str, environment: dict) -> bool:
    probe = subprocess.run([python, "-c", f"import {module}"], env=environment, check=False, capture_output=True)
    return probe.returncode != 0


def _test_artifacts(repo_root: Path) -> set[Path]:
    return {path.resolve() for path in repo_root.glob(".test_*")}


def _remove_path_with_retry(
    path: Path,
    *,
    attempts: int = CLEANUP_ATTEMPTS,
    initial_delay_seconds: float = CLEANUP_INITIAL_DELAY_SECONDS,
) -> bool:
    """Remove a path and confirm disappearance, tolerating short Windows handle delays."""
    for attempt in range(attempts):
        try:
            if path.is_symlink() or not path.is_dir():
                path.unlink(missing_ok=True)
            else:
                shutil.rmtree(path)
        except OSError:
            pass
        if not path.exists():
            return True
        if attempt + 1 < attempts:
            time.sleep(min(initial_delay_seconds * (2**attempt), 1.0))
    return not path.exists()


def _cleanup_new_test_artifacts(
    repo_root: Path,
    baseline: set[Path],
    *,
    attempts: int = CLEANUP_ATTEMPTS,
) -> tuple[list[str], set[Path]]:
    """Remove new repo debris and return the still-existing set for the next attribution pass."""
    failures: list[str] = []
    root = repo_root.resolve()
    for path in sorted(_test_artifacts(repo_root) - baseline):
        if path.parent != root:
            failures.append(f"outside-root:{path}")
            continue
        if not _remove_path_with_retry(path, attempts=attempts):
            failures.append(str(path.relative_to(root)))
    return failures, _test_artifacts(repo_root)


def _make_test_run_root() -> Path:
    configured_root = os.environ.get("TRACKER_TEST_TMP_ROOT")
    parent = Path(configured_root).expanduser().resolve() if configured_root else Path(tempfile.gettempdir()).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="ai-token-tracker-tests-", dir=parent)).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--include-live", action="store_true")
    parser.add_argument("--skip-lint", action="store_true", help="skip the Ruff gate")
    args = parser.parse_args()

    tests_dir = Path(__file__).resolve().parent
    repo_root = tests_dir.parent
    tests = sorted(tests_dir.glob(args.pattern))
    if args.include_live:
        tests.extend(sorted((tests_dir / "live").glob(args.pattern)))

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(repo_root) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
    environment["TRACKER_REPO_ROOT"] = str(repo_root)
    artifact_baseline = _test_artifacts(repo_root)
    failures: list[str] = []
    run_root = _make_test_run_root()
    workspace_cleanup_failed = False
    if not args.skip_lint:
        failures.extend(_run_lint_gate(repo_root, environment))
    try:
        for test_index, test in enumerate(tests):
            print(f"\n=== {test.relative_to(tests_dir)} ===")
            # Keep this deliberately short. Test scripts often create nested paths,
            # and verbose workspace names can exceed the Windows MAX_PATH boundary.
            test_workspace = run_root / f"{TEST_WORKSPACE_PREFIX}{test_index:03d}"
            test_workspace.mkdir(parents=True, exist_ok=False)
            test_environment = dict(environment)
            test_environment["TRACKER_TEST_NAME"] = str(test.relative_to(tests_dir))
            test_environment["TRACKER_TEST_WORKSPACE"] = str(test_workspace)
            result = subprocess.run(
                [sys.executable, str(test)],
                cwd=test_workspace,
                env=test_environment,
                check=False,
            )
            if result.returncode:
                failures.append(str(test.relative_to(tests_dir)))
            if not _remove_path_with_retry(test_workspace):
                workspace_cleanup_failed = True
                failures.append(f"cleanup:{test.name}:workspace")
            cleanup_failures, artifact_baseline = _cleanup_new_test_artifacts(repo_root, artifact_baseline)
            failures.extend(f"cleanup:{test.name}:{path}" for path in cleanup_failures)
    finally:
        if not _remove_path_with_retry(run_root) and not workspace_cleanup_failed:
            failures.append(f"cleanup:run-root:{run_root}")

    print(f"\nExecuted {len(tests)} test scripts + lint gate; failures: {len(failures)}")
    if failures:
        print("Failed:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
