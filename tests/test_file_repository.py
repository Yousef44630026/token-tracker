"""Extra — JSONL FileRepository round-trip (INV-1 / INV-2).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_file_repository.py

The repository is append-only, stores SOURCE-OF-TRUTH fields only, and re-derives totals on
read. Verifies no derived key ever lands on disk and that appends accumulate.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
DERIVED = {"included_in_total", "quantity_in_total", "export_warning", "event_contributing_tokens", "event_total_mismatch"}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def evt(eid, out_qty):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=f"r-{eid}",
        trace_id="t-1",
        span_id="s-1",
        provider="openai",
        api_surface="responses",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, out_qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=out_qty,
    )


path = os.path.join(tempfile.mkdtemp(prefix="tt_repo_"), "events.jsonl")
repo = FileRepository(path)

# read before any write -> empty
check(repo.read_all() == [], "read_all on a missing file returns []")

repo.append(evt("a", 100))
repo.append_many([evt("b", 200), evt("c", 300)])

# appends accumulate (not overwrite)
back = repo.read_all()
check([e.event_id for e in back] == ["a", "b", "c"], "appends accumulate in order")
check(back[2].event_contributing_tokens == 300, "read-back re-derives event_contributing_tokens")

# raw file never contains a derived key
with open(path, encoding="utf-8") as f:
    lines = [json.loads(line) for line in f if line.strip()]
offenders = set()
for obj in lines:
    offenders |= DERIVED & set(obj.keys())
    for q in obj.get("quantities", []):
        offenders |= DERIVED & set(q.keys())
check(offenders == set(), f"no derived key on disk (offenders: {offenders})")

# stored fields survive the round-trip
check(back[0].provider == "openai" and back[0].quantities[0].quantity == 100, "stored fields round-trip intact")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
