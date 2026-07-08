"""Extra — load: many events roll up and export correctly (and quickly).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_load_events.py

Builds 10k events (every 10th superseded), checks the rollup / coverage / CSV export all
agree on the contributing total at scale, and that it completes well under a generous bound.
"""

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0
N = 10000
TIME_BUDGET_S = 30.0  # generous; only catches pathological blow-ups


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, qty):
    return TokenQuantity(tt, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


t0 = time.perf_counter()
trace = Trace(trace_id="load")
live = 0
for i in range(N):
    superseded = i % 10 == 0
    trace.add_event(
        TokenEvent(
            event_id=f"e{i}",
            request_correlation_id=f"r{i}",
            trace_id="load",
            span_id="s",
            provider="openai",
            api_surface="chat_completions",
            quantities=[q(TokenType.INPUT, 100), q(TokenType.OUTPUT, 50)],
            provider_total_tokens=150,
            superseded=superseded,
            superseded_by=("final" if superseded else None),
        )
    )
    if not superseded:
        live += 1
build_s = time.perf_counter() - t0
expected = live * 150

t1 = time.perf_counter()
total = observed_total_contributing_tokens(trace)
rollup_s = time.perf_counter() - t1
check(total == expected, f"rollup total over {N} events == {expected} (got {total})")

cov = build_coverage_exactness(trace)
check(cov["event_count"] == live, f"coverage counts the {live} non-superseded events")
check(cov["excluded_event_count"] == N // 10, "coverage counts excluded superseded events")
check(cov["superseded_event_count"] == N // 10, "coverage counts the superseded subset")
check(cov["observed_total_contributing_tokens"] == expected, "coverage total agrees with the rollup")

t2 = time.perf_counter()
out_dir = os.path.join(os.getcwd(), ".test_load_events")
os.makedirs(out_dir, exist_ok=True)
paths = export_csv(trace, out_dir)
export_s = time.perf_counter() - t2
with open(paths["token_events"], newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check(len(rows) == N, f"events CSV has {N} rows")
csv_total = sum(int(r["event_contributing_tokens"]) for r in rows)
check(csv_total == expected, "exported CSV total agrees at scale")

total_s = time.perf_counter() - t0
check(total_s < TIME_BUDGET_S, f"completed under {TIME_BUDGET_S:.0f}s (took {total_s:.2f}s)")
print(f"  timings: build={build_s:.2f}s rollup={rollup_s:.3f}s export={export_s:.2f}s total={total_s:.2f}s")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
