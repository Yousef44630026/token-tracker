"""Collector soak quantifies downtime, regressions, and store-prefix integrity."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.collector_soak import run_soak  # noqa: E402

check = make_checker()
root = os.path.abspath(f".test_collector_soak_{uuid.uuid4().hex}")
store = os.path.join(root, "collector.jsonl")
os.makedirs(root)
with open(store, "w", encoding="utf-8") as handle:
    handle.write('{"event_id":"one"}\n')


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def sequence_probe(samples: list[dict]):
    remaining = iter(samples)

    def probe(**kwargs):
        return next(remaining)

    return probe


clock = FakeClock()
mixed = run_soak(
    base_url="http://127.0.0.1:8787",
    store_path=store,
    output_dir=os.path.join(root, "mixed"),
    duration_seconds=100,
    interval_seconds=1,
    max_samples=3,
    auth_token="must-not-appear",
    probe=sequence_probe(
        [
            {"healthy": True, "events": 1, "total": 10, "latency_ms": 10.0},
            {"healthy": False, "status": "offline", "latency_ms": 20.0},
            {"healthy": True, "events": 2, "total": 12, "latency_ms": 30.0},
        ]
    ),
    monotonic=clock.monotonic,
    wall_clock=clock.monotonic,
    sleep=clock.sleep,
)
check(mixed["samples"] == 3, "soak honors the bounded sample target")
check(mixed["uptime_ratio"] == 0.666667, "soak quantifies observed uptime")
check(mixed["outage_count"] == 1, "soak groups consecutive failures into outages")
check(mixed["latency_ms"]["p95"] == 30.0, "soak reports bounded latency percentiles")
check(mixed["passed"] is False, "any observed outage fails the strict soak verdict")

summary_text = open(mixed["artifacts"]["summary"], encoding="utf-8").read()
check("must-not-appear" not in summary_text, "soak summary never stores the auth token")

clock = FakeClock()
healthy = run_soak(
    base_url="http://127.0.0.1:8787",
    store_path=store,
    output_dir=os.path.join(root, "healthy"),
    duration_seconds=100,
    interval_seconds=1,
    max_samples=2,
    probe=sequence_probe(
        [
            {"healthy": True, "events": 2, "total": 12, "latency_ms": 2.0},
            {"healthy": True, "events": 3, "total": 15, "latency_ms": 3.0},
        ]
    ),
    monotonic=clock.monotonic,
    wall_clock=clock.monotonic,
    sleep=clock.sleep,
)
check(healthy["passed"] is True, "healthy probes with an unchanged store prefix pass")
check(
    healthy["evidence_type"] == "collector_soak" and len(healthy["runtime_fingerprint"]) == 64,
    "soak evidence is typed and bound to the current runtime",
)
check(healthy["store_integrity"]["verified"] is True, "soak verifies the pre-existing store prefix")
check(healthy["collector_counters"]["regressions"] == 0, "monotonic collector counters remain clean")


class SuspendingClock(FakeClock):
    def sleep(self, seconds: float) -> None:
        self.value += 10.0


suspending_clock = SuspendingClock()
suspended = run_soak(
    base_url="http://127.0.0.1:8787",
    store_path=store,
    output_dir=os.path.join(root, "suspended"),
    duration_seconds=10,
    interval_seconds=1,
    probe=sequence_probe(
        [
            {"healthy": True, "events": 3, "total": 15, "latency_ms": 1.0},
            {"healthy": True, "events": 3, "total": 15, "latency_ms": 1.0},
        ]
    ),
    monotonic=suspending_clock.monotonic,
    wall_clock=suspending_clock.monotonic,
    sleep=suspending_clock.sleep,
)
check(suspended["sampling"]["sample_gap_count"] == 1, "sleep/resume gap is measured explicitly")
check(suspended["sampling"]["capture_ratio"] < 0.95, "missing scheduled probes reduce capture completeness")
check(suspended["passed"] is False, "a mostly sleeping soak cannot report a passing verdict")

mutating_calls = 0


def mutating_probe(**kwargs):
    global mutating_calls
    mutating_calls += 1
    if mutating_calls == 2:
        with open(store, "w", encoding="utf-8") as handle:
            handle.write('{"event_id":"changed"}\n')
    return {"healthy": True, "events": mutating_calls, "total": mutating_calls, "latency_ms": 1.0}


clock = FakeClock()
mutated = run_soak(
    base_url="http://127.0.0.1:8787",
    store_path=store,
    output_dir=os.path.join(root, "mutated"),
    duration_seconds=100,
    interval_seconds=1,
    max_samples=2,
    probe=mutating_probe,
    monotonic=clock.monotonic,
    wall_clock=clock.monotonic,
    sleep=clock.sleep,
)
check(mutated["store_integrity"]["prefix_unchanged"] is False, "in-place store mutation is detected")
check(mutated["passed"] is False, "store-prefix mutation fails the soak verdict")
check(json.loads(summary_text)["schema_version"] == 2, "soak summary is explicitly versioned")

shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_collector_soak"))
