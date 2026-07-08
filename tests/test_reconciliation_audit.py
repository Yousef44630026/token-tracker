"""Verification audit - every realistic fixture reconciles through normalize().

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_reconciliation_audit.py

Discovers every *.SIMULATED.json and *.REAL.json fixture under tests/fixtures/realistic.
Each fixture must be explicitly mapped to an adapter so future fixtures cannot escape the
audit by accident.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.validation.fixture_manifest import REALISTIC_FIXTURE_ADAPTERS  # noqa: E402

_failures = 0
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "realistic"


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


FIXTURE_ADAPTERS = REALISTIC_FIXTURE_ADAPTERS


def fixture_paths():
    paths = sorted(FIXTURES.glob("*.SIMULATED.json")) + sorted(FIXTURES.glob("*.REAL.json"))
    return sorted(paths, key=lambda path: path.name)


def responses_from_payload(path, payload):
    if "cases" in payload:
        for case in payload["cases"]:
            yield f"{path.name}:{case.get('family', 'case')}", case["response"]
        return
    if "response" in payload:
        yield path.name, payload["response"]
        return
    # Streaming capture (family B): the auditable response is the final usage-bearing chunk.
    captured = payload.get("captured")
    if isinstance(captured, list):
        usage_chunk = next((chunk for chunk in captured if isinstance(chunk, dict) and chunk.get("usage")), None)
        if usage_chunk is not None:
            yield path.name, usage_chunk
        return
    if isinstance(captured, dict):
        yield path.name, captured
        return
    # RAG pipeline capture: the auditable response is the final generation.
    if "generation" in payload:
        yield path.name, payload["generation"]
        return
    # RAG control (two-arm): both the with-context and without-context responses reconcile.
    if "with_context" in payload:
        yield f"{path.name}:with_context", payload["with_context"]
        yield f"{path.name}:without_context", payload["without_context"]
        return


paths = fixture_paths()
check(bool(paths), "realistic fixture discovery found files")
check(
    {path.name for path in paths} == set(FIXTURE_ADAPTERS),
    "every realistic SIMULATED/REAL fixture has an explicit adapter mapping",
)

for path in paths:
    adapter_type = FIXTURE_ADAPTERS.get(path.name)
    if adapter_type is None:
        check(False, f"{path.name}: no mapped adapter")
        continue
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    for label, response in responses_from_payload(path, payload):
        event = normalize(response, adapter_type(), context=new_trace())
        qit_sum = sum(quantity.quantity_in_total for quantity in event.quantities)
        check(event.event_total_mismatch in (0, None), f"{label}: event_total_mismatch is reconciled")
        check(qit_sum == event.event_contributing_tokens, f"{label}: sum(quantity_in_total) == event_contributing_tokens")
        check(
            all(quantity.quantity is None or quantity.quantity >= 0 for quantity in event.quantities),
            f"{label}: no negative quantities",
        )
        check(
            all(quantity.quantity_in_total == 0 for quantity in event.quantities if quantity.additivity == Additivity.SUBTOTAL_OF),
            f"{label}: every subtotal_of quantity contributes 0",
        )
        if event.provider_total_tokens is not None:
            check(event.provider_total_tokens == qit_sum, f"{label}: provider_total equals sum(quantity_in_total)")

# Drift defense: renamed/dropped usage fields must raise an explicit flag, not a silent total.
renamed_field = {
    "model": "gpt-4o-audit",
    "usage": {
        "prompt_tokenz": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    },
}
event = normalize(renamed_field, OpenAIChatCompletionsAdapter(), context=new_trace())
check("provider_total_mismatch" in event.data_quality_flags, "renamed token field raises provider_total_mismatch")
check(event.event_contributing_tokens == 20, "renamed token field never fabricates the missing input count")

dropped_usage = {"model": "gpt-4o-audit", "choices": []}
event = normalize(dropped_usage, OpenAIChatCompletionsAdapter(), context=new_trace())
check("raw_usage_missing" in event.data_quality_flags, "dropped usage raises raw_usage_missing")
check(event.event_contributing_tokens == 0, "dropped usage contributes 0")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
