"""Regression — a missing/empty observation must default to authoritative, not be rejected.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_observation_default_backward_compat.py

The explicit-``authoritative`` requirement (INV-7) guards against a TYPO when observation
metadata is present (e.g. ``authoratative`` must not silently default into the total). It must
NOT reject an event that carries no observation at all — that is the common case for minimal
collector payloads and every legacy JSONL row written before the observation field existed.

This falsifies the regression where TokenEvent.from_dict routed an absent observation through
``Observation.from_dict({})`` and raised "observation.authoritative must be explicit", which
made the collector silently drop valid events and made pre-existing JSONL data unreadable.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def minimal_dict(eid: str) -> dict:
    """A minimal event with NO observation key — the shape a lean client or legacy row has."""
    return {
        "event_id": eid,
        "request_correlation_id": "r",
        "trace_id": "t",
        "span_id": "s",
        "quantities": [
            {
                "token_type": "output",
                "quantity": 7,
                "precision_level": "exact",
                "usage_source": "provider_response",
                "additivity": "total_contributing",
            }
        ],
        "provider_total_tokens": 7,
    }


# 1. No observation key at all -> loads, defaults to authoritative, contributes.
ev = TokenEvent.from_dict(minimal_dict("no-obs"))
check(ev.is_authoritative is True, "event with no observation defaults to authoritative")
check(ev.event_contributing_tokens == 7, "authoritative-by-default event contributes its tokens")

# 2. An EXPLICIT empty observation dict is a caller handing metadata-shaped nothing; it is
#    still rejected (INV-7) so authority never silently defaults into totals. Only a fully
#    ABSENT observation (case 1) defaults — an explicit {} does not.
d_empty = minimal_dict("empty-obs")
d_empty["observation"] = {}
rejected_empty = False
try:
    TokenEvent.from_dict(d_empty)
except ValueError:
    rejected_empty = True
check(rejected_empty, "explicit observation={} is rejected, not silently defaulted (absent != empty)")

# 3. The typo guard is PRESERVED: non-empty observation metadata missing authoritative -> reject.
d_typo = minimal_dict("typo-obs")
d_typo["observation"] = {"status": "complete", "authoratative": True}  # misspelled on purpose
raised = False
try:
    TokenEvent.from_dict(d_typo)
except ValueError:
    raised = True
check(raised, "non-empty observation missing 'authoritative' is still rejected (typo guard intact)")

# 4. A non-authoritative event with an explicit observation still loads and is forced to 0.
d_nonauth = minimal_dict("nonauth")
d_nonauth["observation"] = {"authoritative": False, "status": "failed"}
ev_nonauth = TokenEvent.from_dict(d_nonauth)
check(ev_nonauth.is_authoritative is False, "explicit authoritative=False is honored")
check(ev_nonauth.event_contributing_tokens == 0, "non-authoritative event contributes 0")

# 5. Round-trip through real JSONL: a legacy row (no observation key) must read back.
with tempfile.TemporaryDirectory(prefix="tt_obs_compat_") as d:
    path = os.path.join(d, "legacy.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(minimal_dict("legacy-row")) + "\n")
    repo = FileRepository(path)
    events = repo.read_all()
    check(len(events) == 1, "legacy JSONL row (no observation) reads back")
    check(events[0].event_contributing_tokens == 7, "legacy row still contributes its tokens")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
