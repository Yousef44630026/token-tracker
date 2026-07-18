"""Falsify silent tokenizer degradation.

Run: python tests/test_tokenizer_required.py
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker.ops.doctor as doctor_module  # noqa: E402
from tracker.estimation.local_tokenizer import tokenizer_status  # noqa: E402

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
dependencies = project["project"]["dependencies"]
check(any(item.split(">=", 1)[0] == "tiktoken" for item in dependencies), "fresh installs require tiktoken in core dependencies")
check(tokenizer_status()["tokenizer_available"] is True, "installed runtime selects the required tiktoken backend")

original_status = doctor_module.tokenizer_status
try:
    doctor_module.tokenizer_status = lambda: {
        "backend": "tracker_char4_fallback",
        "tokenizer_available": False,
        "fallback_characters_per_token": 4,
    }
    fallback_check = doctor_module._tokenizer_check()
finally:
    doctor_module.tokenizer_status = original_status

check(fallback_check.status == "fail", "Doctor fails when the emergency char4 fallback would be active")
check(fallback_check.data["backend"] == "tracker_char4_fallback", "Doctor identifies the degraded backend")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
raise SystemExit(1 if _failures else 0)
