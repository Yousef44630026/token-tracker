"""Extra — load: JSONL repository at scale (Phase 2).

Run: python tests/test_load_storage.py

Appends 5k events, reads them back, and checks the count, the re-derived total, and that no
derived key leaked onto disk — at scale.
"""

import json
import os
import shutil
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
N = 5000
# Must stay in sync with the model's derived @property fields (see the introspective backstop
# in test_storage_no_stored_derived_fields.py). under/over_attributed_tokens were added later.
DERIVED = {
    "included_in_total",
    "quantity_in_total",
    "export_warning",
    "event_contributing_tokens",
    "event_total_mismatch",
    "under_attributed_tokens",
    "over_attributed_tokens",
}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, qty):
    return TokenQuantity(tt, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


events = [
    TokenEvent(
        event_id=f"e{i}",
        request_correlation_id=f"r{i}",
        trace_id="load",
        span_id="s",
        provider="openai",
        api_surface="chat_completions",
        quantities=[q(TokenType.INPUT, 100), q(TokenType.OUTPUT, 50)],
        provider_total_tokens=150,
        observation={"authoritative": True},
    )
    for i in range(N)
]

work = os.path.join(os.getcwd(), ".test_load_storage")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work, exist_ok=True)
path = os.path.join(work, f"events-{uuid.uuid4().hex}.jsonl")
repo = FileRepository(path)

t0 = time.perf_counter()
repo.append_many(events)
write_s = time.perf_counter() - t0

t1 = time.perf_counter()
back = repo.read_all()
read_s = time.perf_counter() - t1

check(len(back) == N, f"read back all {N} events")
check(sum(e.event_contributing_tokens for e in back) == N * 150, "re-derived total correct at scale")
check(back[0].event_id == "e0" and back[-1].event_id == f"e{N - 1}", "order preserved")

# spot-check the raw file for derived-key leakage
with open(path, encoding="utf-8") as f:
    first = json.loads(f.readline())
check(DERIVED.isdisjoint(first.keys()), "no derived key on disk (event level)")
check(all(DERIVED.isdisjoint(q.keys()) for q in first["quantities"]), "no derived key on disk (quantity level)")

print(f"  timings: write {N} in {write_s:.2f}s, read in {read_s:.2f}s")
print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if _failures else 0)
