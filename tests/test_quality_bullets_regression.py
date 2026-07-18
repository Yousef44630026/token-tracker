"""Regression — the 3-bullet quality check must recognize the real Unicode bullet "•".

Run: python tests/test_quality_bullets_regression.py

Found during a rigorous review of tracker/proxy/quality.py: _three_bullets_exact matched the
mojibake string "â€¢" (U+2022's UTF-8 bytes E2 80 A2 misdecoded as Latin-1) instead of the real
bullet "•" (U+2022) that its sibling checks _four_bullets/_five_bullets use. A response using
genuine "•" bullets was counted as 0 bullets and failed the "Workspace read-only context"
quality check spuriously (a false negative on a correct model response).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.proxy.quality import _four_bullets, _three_bullets_exact, check_prompt_output  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


BULLET = "•"  # the real Unicode bullet, explicitly, so this file's own encoding can't hide the point

# three genuine Unicode bullets -> must PASS (return None)
three = f"{BULLET} first\n{BULLET} second\n{BULLET} third"
check(_three_bullets_exact(three) is None, f"FIXED: three real '{BULLET}' bullets pass the 3-bullet check (was a false failure)")

# dash and star bullets still accepted
check(_three_bullets_exact("- a\n- b\n- c") is None, "dash bullets still accepted")
check(_three_bullets_exact("* a\n* b\n* c") is None, "star bullets still accepted")

# wrong count still fails
check(_three_bullets_exact(f"{BULLET} a\n{BULLET} b") is not None, "two bullets still correctly fails the exactly-3 check")

# consistency with the sibling check that was always correct
check(
    _four_bullets(f"{BULLET} a\n{BULLET} b\n{BULLET} c\n{BULLET} d") is None,
    "sibling _four_bullets already matched the real bullet (sanity)",
)

# end-to-end through the public entry point for the label that uses this rule
result = check_prompt_output(sequence=9, label="Workspace read-only context", stdout=three)
check(result.passed, "end-to-end: a 3-real-bullet response passes the 'Workspace read-only context' quality check")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
