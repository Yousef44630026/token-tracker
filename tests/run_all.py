"""Run every non-live test script with the current Python interpreter."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


def _cleanup_new_test_artifacts(repo_root: Path, baseline: set[Path]) -> list[str]:
    """Remove only artifacts created after this run started."""
    failures: list[str] = []
    root = repo_root.resolve()
    for path in sorted(_test_artifacts(repo_root) - baseline):
        if path.parent != root:
            failures.append(f"outside-root:{path}")
            continue
        for attempt in range(5):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                break
            except OSError:
                if attempt == 4:
                    failures.append(str(path.relative_to(root)))
                else:
                    time.sleep(0.05 * (attempt + 1))
    return failures


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
    artifact_baseline = _test_artifacts(repo_root)
    failures: list[str] = []
    if not args.skip_lint:
        failures.extend(_run_lint_gate(repo_root, environment))
    for test in tests:
        print(f"\n=== {test.relative_to(tests_dir)} ===")
        result = subprocess.run([sys.executable, str(test)], env=environment, check=False)
        if result.returncode:
            failures.append(str(test.relative_to(tests_dir)))
        cleanup_failures = _cleanup_new_test_artifacts(repo_root, artifact_baseline)
        failures.extend(f"cleanup:{test.name}:{path}" for path in cleanup_failures)

    print(f"\nExecuted {len(tests)} test scripts + lint gate; failures: {len(failures)}")
    if failures:
        print("Failed:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
