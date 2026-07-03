"""Extra — normalizer extras (keystone).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_normalizer_more.py

event_id override, hashes/timestamp passthrough, model passthrough, explicit context wins
over ambient, and a clean fallback to a fresh root when there is no context at all.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import current, new_trace, span, trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


with open(os.path.join(FIXTURES, "openai_chat_completions_cached_reasoning.SIMULATED.json"), encoding="utf-8") as f:
    payload = json.load(f)["response"]
adapter = OpenAIChatCompletionsAdapter()

# --- event_id override + hashes/timestamp passthrough ---
ev = normalize(
    payload, adapter, context=new_trace(), event_id="my-id", request_hash="rh", response_hash="sh", timestamp="2026-06-24T10:00:00"
)
check(ev.event_id == "my-id", "event_id override honored")
check(ev.request_hash == "rh" and ev.response_hash == "sh", "request/response hashes passed through")
check(ev.timestamp == "2026-06-24T10:00:00", "timestamp passed through")

# --- model passthrough from the adapter ---
check(ev.model == "o4-mini-2025-04-16", "model passed through from the payload")

# --- explicit context wins over the ambient context ---
explicit = new_trace(workflow="explicit")
with trace(workflow="ambient"):
    with span():
        amb = current()
        ev2 = normalize(payload, adapter, context=explicit)
check(ev2.trace_id == explicit.trace_id and ev2.workflow == "explicit", "explicit context overrides ambient")
check(ev2.trace_id != amb.trace_id, "ambient context was NOT used when explicit is given")

# --- no context + no ambient -> fresh root, identity still populated ---
check(current() is None, "no ambient context outside a trace block")
ev3 = normalize(payload, adapter)
check(bool(ev3.trace_id) and bool(ev3.span_id) and bool(ev3.request_correlation_id), "fallback root has full identity")
check(ev3.event_contributing_tokens == 1300, "fallback-root event still computes correctly")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
