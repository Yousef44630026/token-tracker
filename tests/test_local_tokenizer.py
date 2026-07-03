"""Extra — local token estimator (Phase 7).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_local_tokenizer.py

A coarse ~4-chars/token heuristic used ONLY for partial-stream estimates: 0 for empty, at
least 1 for any non-empty text, and monotonic in length.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.estimation.local_tokenizer import estimate_tokens  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


check(estimate_tokens("") == 0, "empty string -> 0 tokens")
check(estimate_tokens("x") == 1, "1 char -> at least 1 token")
check(estimate_tokens("abcd") == 1, "4 chars -> ~1 token")
check(estimate_tokens("abcdefgh") == 2, "8 chars -> ~2 tokens")
check(estimate_tokens("a" * 100) == 25, "100 chars -> ~25 tokens")
check(estimate_tokens("a" * 1000) > estimate_tokens("a" * 10), "monotonic: more text -> more tokens")
check(estimate_tokens("hello world") >= 1, "real text is non-zero")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
