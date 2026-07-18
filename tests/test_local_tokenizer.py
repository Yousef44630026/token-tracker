"""Best-available local token estimator and disclosed fallback behavior.

Run: python tests/test_local_tokenizer.py

The runtime prefers tiktoken and falls back to a deterministic four-character heuristic.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.estimation.local_tokenizer import (  # noqa: E402
    estimate_tokens,
    estimate_tokens_char4,
    estimate_with_metadata,
    tokenizer_status,
)

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


check(estimate_tokens("") == 0, "empty string -> 0 tokens")
check(estimate_tokens("x") == 1, "1 char -> at least 1 token")
check(estimate_tokens("a" * 1000) > estimate_tokens("a" * 10), "monotonic: more text -> more tokens")
check(estimate_tokens("hello world") >= 1, "real text is non-zero")
check(estimate_tokens_char4("abcd") == 1, "char4 fallback remains deterministic")
check(estimate_tokens_char4("a" * 100) == 25, "char4 fallback discloses its 4-character ratio")
estimate = estimate_with_metadata("hello world")
check(estimate.quantity == estimate_tokens("hello world"), "metadata and compatibility APIs agree")
check(estimate.text_characters == 11, "estimate metadata records input size without retaining text")
check(
    estimate.estimator in {"tokentap_cl100k_base", "tracker_char4_fallback"},
    "estimate metadata names the active backend",
)
status = tokenizer_status()
check(status["backend"] == estimate.estimator, "doctor-facing tokenizer status matches event estimates")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
