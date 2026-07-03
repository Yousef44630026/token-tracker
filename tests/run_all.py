"""Run every non-live test script with the current Python interpreter."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _run_lint_gate(repo_root: Path, environment: dict) -> list[str]:
    """Run ruff + black --check over the repo. Returns a list of failure labels.

    CLAUDE.md requires ruff+black clean after every phase, but nothing enforced that
    automatically — it depended on remembering to run them by hand. If either tool isn't
    installed in this interpreter, that is reported as a skip (not a silent pass), so the
    gap stays visible instead of quietly not protecting anything.
    """
    failures: list[str] = []
    for label, cmd in (
        ("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        ("black --check", [sys.executable, "-m", "black", "--check", "."]),
    ):
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--include-live", action="store_true")
    parser.add_argument("--skip-lint", action="store_true", help="skip the ruff/black gate")
    args = parser.parse_args()

    tests_dir = Path(__file__).resolve().parent
    repo_root = tests_dir.parent
    tests = sorted(tests_dir.glob(args.pattern))
    if args.include_live:
        tests.extend(sorted((tests_dir / "live").glob(args.pattern)))

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    failures: list[str] = []
    if not args.skip_lint:
        failures.extend(_run_lint_gate(repo_root, environment))
    for test in tests:
        print(f"\n=== {test.relative_to(tests_dir)} ===")
        result = subprocess.run([sys.executable, str(test)], env=environment, check=False)
        if result.returncode:
            failures.append(str(test.relative_to(tests_dir)))

    print(f"\nExecuted {len(tests)} test scripts + lint gate; failures: {len(failures)}")
    if failures:
        print("Failed:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
