"""Extra — the shared adapter field accessor (base.field_value).

Run: python tests/test_field_value.py

field_value reads a field from either a decoded mapping (dict) or an SDK object (attribute),
returning the default when absent. Every adapter relies on it, so its contract is pinned here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.base import field_value  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# --- dict access ---
check(field_value({"a": 1}, "a") == 1, "dict: present key returned")
check(field_value({"a": 1}, "b") is None, "dict: missing key -> default None")
check(field_value({"a": 1}, "b", 99) == 99, "dict: missing key -> custom default")
check(field_value({"a": 0}, "a", 99) == 0, "dict: falsy value (0) returned, not the default")
check(field_value({"a": None}, "a", 99) is None, "dict: explicit None returned, not the default")


# --- object (SDK-style) access ---
class Usage:
    def __init__(self):
        self.prompt_tokens = 5


u = Usage()
check(field_value(u, "prompt_tokens") == 5, "object: present attribute returned")
check(field_value(u, "missing") is None, "object: missing attribute -> default None")
check(field_value(u, "missing", "d") == "d", "object: missing attribute -> custom default")

# --- nested access pattern used by the adapters (details sub-object) ---
nested = {"prompt_tokens_details": {"cached_tokens": 800}}
check(field_value(field_value(nested, "prompt_tokens_details", {}), "cached_tokens") == 800, "nested dict access composes")
check(field_value(field_value(nested, "absent_details", {}) or {}, "cached_tokens") is None, "missing nested object -> None, no crash")

# --- robust on odd inputs ---
check(field_value(None, "x") is None, "None object -> default (no crash)")
check(field_value(123, "x", "fallback") == "fallback", "non-mapping/non-object -> default")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
