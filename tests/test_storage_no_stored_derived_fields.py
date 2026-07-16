"""Phase 2 / step 2 — CORE FALSIFIER: derived fields never hit storage (INV-1 / INV-2).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_storage_no_stored_derived_fields.py

Round-trip a TokenEvent through real JSONL on disk and assert:
  - the serialized JSON contains ONLY source-of-truth keys (no derived keys at any depth)
  - the read-back object DERIVES included_in_total / quantity_in_total / export_warning /
    event_contributing_tokens / event_total_mismatch — they were recomputed, not stored.
"""

import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import (  # noqa: E402
    Additivity,
    PrecisionLevel,
    TokenType,
    UsageSource,
)
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0

DERIVED_KEYS = {
    "included_in_total",
    "quantity_in_total",
    "export_warning",
    "event_contributing_tokens",
    "event_total_mismatch",
    "under_attributed_tokens",
    "over_attributed_tokens",
}


def _model_derived_property_names() -> set[str]:
    """Every @property on the stored models = the set of fields that MUST stay derived.

    Introspected, not hand-listed: the hand-maintained DERIVED_KEYS above silently drifted
    once (under/over_attributed_tokens were added to the model and to doctor.py but never
    back-ported here), which would let a future to_dict() regression on a new derived field
    pass this falsifier green. Deriving the set from the classes makes that drift impossible.
    """
    return {name for cls in (TokenEvent, TokenQuantity) for name, attr in vars(cls).items() if isinstance(attr, property)}


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def deep_keys(obj) -> set:
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(k)
            found |= deep_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= deep_keys(v)
    return found


def make_event() -> TokenEvent:
    return TokenEvent(
        event_id="ev-1",
        request_correlation_id="corr-1",
        trace_id="tr-1",
        span_id="sp-1",
        parent_span_id=None,
        business_id="biz",
        workflow="wf",
        environment="prod",
        provider="openai",
        model="gpt-x",
        api_surface="responses",
        provider_total_tokens=150,
        quantities=[
            TokenQuantity(
                token_type=TokenType.INPUT,
                quantity=100,
                precision_level=PrecisionLevel.EXACT,
                usage_source=UsageSource.PROVIDER_RESPONSE,
                additivity=Additivity.TOTAL_CONTRIBUTING,
            ),
            TokenQuantity(
                token_type=TokenType.OUTPUT,
                quantity=50,
                precision_level=PrecisionLevel.EXACT,
                usage_source=UsageSource.PROVIDER_RESPONSE,
                additivity=Additivity.TOTAL_CONTRIBUTING,
            ),
            TokenQuantity(
                token_type=TokenType.CACHED_INPUT,
                quantity=40,
                precision_level=PrecisionLevel.EXACT,
                usage_source=UsageSource.PROVIDER_RESPONSE,
                additivity=Additivity.SUBTOTAL_OF,
                subtotal_of="input",
            ),
        ],
        data_quality_flags=[],
        observation={
            "status": "complete",
            "authoritative": True,
            "http_status": 200,
            "provider_request_id": "req-1",
        },
    )


def main() -> int:
    ev = make_event()

    # sanity: the model itself derives correctly before any storage
    check(ev.event_contributing_tokens == 150, "event sums only quantity_in_total (cached excluded)")
    check(ev.event_total_mismatch == 0, "event_total_mismatch == 0 when provider total matches")

    tmpdir = os.path.join(os.getcwd(), ".test_tracker_store")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)
    path = os.path.join(tmpdir, f"events-{uuid.uuid4().hex}.jsonl")
    repo = FileRepository(path)
    repo.append(ev)

    # --- inspect the raw bytes on disk ---
    with open(path, encoding="utf-8") as fh:
        line = fh.readline().strip()
    raw = json.loads(line)
    all_keys = deep_keys(raw)
    check(
        DERIVED_KEYS.isdisjoint(all_keys),
        f"no derived key serialized to JSONL (found offenders: {DERIVED_KEYS & all_keys})",
    )
    # Anti-drift backstop: catch ANY derived @property leaking, including ones not yet added
    # to the hand list above, so the falsifier cannot silently go stale as the model grows.
    derived_properties = _model_derived_property_names()
    check(
        derived_properties.isdisjoint(all_keys),
        f"no derived @property leaks into JSONL (offenders: {sorted(derived_properties & all_keys)})",
    )
    check("event_id" in all_keys and "quantities" in all_keys, "stored keys ARE present in JSONL")
    check(
        raw["observation"]["provider_request_id"] == "req-1",
        "source-of-truth observation metadata is serialized",
    )

    # --- read back and confirm derivation happens on the rehydrated object ---
    events = repo.read_all()
    check(len(events) == 1, "exactly one event read back")
    rb = events[0]
    check(rb == ev, "read-back event equals the original (stored fields)")
    check(rb.event_contributing_tokens == 150, "read-back DERIVES event_contributing_tokens")
    check(rb.event_total_mismatch == 0, "read-back DERIVES event_total_mismatch")
    check(
        rb.quantities[2].export_warning == "subtotal_excluded_from_total",
        "read-back DERIVES quantity export_warning",
    )

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
