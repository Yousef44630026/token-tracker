"""Built-in adapter discovery and lookup."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters import available_adapters, create_adapter  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


pairs = available_adapters()
check(("openai", "responses") in pairs, "OpenAI Responses is discoverable")
check(("bedrock", "invoke_model") in pairs, "Bedrock InvokeModel is discoverable")
check(create_adapter("OpenAI", "chat-completions").provider == "openai", "lookup normalizes names")
check(
    create_adapter("azure-openai", "responses").provider == "azure_openai",
    "provider aliases resolve",
)

unsupported = False
try:
    create_adapter("unknown", "surface")
except ValueError as exc:
    unsupported = "supported:" in str(exc)
check(unsupported, "unsupported lookup gives a descriptive error")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
