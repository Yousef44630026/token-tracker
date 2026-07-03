"""Phase 3 — token_type purity (INV-3).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_token_type_purity.py

token_type encodes WHAT the tokens are, never how well they were measured. The forbidden
"measurement-leaking" types must not exist as members and must not be constructible.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import TokenType  # noqa: E402

_failures = 0

FORBIDDEN = {"partial_output_observed", "estimated_input", "estimated_output", "total"}


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


values = {m.value for m in TokenType}

check(FORBIDDEN.isdisjoint(values), f"no forbidden token_type is a member (offenders: {FORBIDDEN & values})")

for bad in sorted(FORBIDDEN):
    raised = False
    try:
        TokenType(bad)
    except ValueError:
        raised = True
    check(raised, f"TokenType({bad!r}) is not constructible")

check("output" in values and "input" in values, "the real types (input/output) still exist")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
