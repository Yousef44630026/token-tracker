"""Shared [PASS]/[FAIL] check bookkeeping for plain-script tests (no pytest in this environment).

Each test script tracks its own pass/fail counters and exits 1 on any failure. New test files
should use ``make_checker()`` instead of redefining the same ``check()``/``_failures`` boilerplate.

Existing test files predate this module and are NOT being mass-migrated: ~70 of them redefine
this pattern with small variations (some also track a ``_checks`` counter, some print custom
final summaries), so a mechanical rewrite across all of them risks subtle breakage for a purely
cosmetic gain, and collides with files Codex may be actively editing in parallel. This module
exists so *new* tests do not repeat the boilerplate; retrofitting old ones is a separate,
lower-priority task if ever worth doing file by file.
"""

from __future__ import annotations


class Checker:
    """Tracks PASS/FAIL counts across a test script's assertions."""

    def __init__(self) -> None:
        self.checks = 0
        self.failures = 0

    def __call__(self, cond: object, msg: str) -> bool:
        self.checks += 1
        ok = bool(cond)
        print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
        if not ok:
            self.failures += 1
        return ok

    def report(self, label: str = "RESULT") -> int:
        """Print the final summary and return the process exit code (0 or 1)."""
        print(f"\n{label}:", "all checks passed" if self.failures == 0 else f"{self.failures} FAILURE(S)")
        return 1 if self.failures else 0


def make_checker() -> Checker:
    """Return a fresh ``Checker`` — callable as ``check(cond, msg)``.

    Usage::

        check = make_checker()
        check(1 + 1 == 2, "sanity")
        ...
        sys.exit(check.report())
    """
    return Checker()
