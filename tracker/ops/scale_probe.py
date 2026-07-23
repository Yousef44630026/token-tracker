"""Reproducible JSONL/effective-projection/dashboard scale evidence."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import platform
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict, dataclass
from pathlib import Path

from tracker.derive.effective_events import iter_effective_events
from tracker.export.live_dashboard import aggregate
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.storage.file_repository import FileRepository


@dataclass(frozen=True)
class ScaleProbeReport:
    generated_at: str
    event_count: int
    effective_event_count: int
    total_tokens: int
    store_bytes: int
    write_seconds: float
    projection_seconds: float
    dashboard_seconds: float
    memory_probe_seconds: float
    peak_memory_mb: float
    max_projection_seconds: float
    max_dashboard_seconds: float
    max_peak_memory_mb: float
    passed: bool
    failures: list[str]
    python: str
    platform: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _event(index: int) -> TokenEvent:
    return TokenEvent(
        event_id=f"scale-event-{index:08d}",
        request_correlation_id=f"scale-request-{index:08d}",
        trace_id=f"scale-trace-{index // 1000:08d}",
        span_id=f"scale-span-{index:08d}",
        provider="scale_probe",
        model="synthetic-exact",
        api_surface="benchmark",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                10,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            ),
            TokenQuantity(
                TokenType.OUTPUT,
                5,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            ),
        ],
        provider_total_tokens=15,
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": True, "status": "complete", "service_name": "scale-probe"},
    )


def _write_atomic(path: str, payload: dict[str, object]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def run_probe(
    *,
    event_count: int = 50_000,
    max_projection_seconds: float = 30.0,
    max_dashboard_seconds: float = 30.0,
    max_peak_memory_mb: float = 512.0,
    batch_size: int = 1_000,
    work_dir: str | os.PathLike[str] | None = None,
) -> ScaleProbeReport:
    """Generate isolated evidence; never read or modify the operational ledger."""
    if event_count < 1:
        raise ValueError("event_count must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    for name, value in (
        ("max_projection_seconds", max_projection_seconds),
        ("max_dashboard_seconds", max_dashboard_seconds),
        ("max_peak_memory_mb", max_peak_memory_mb),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be positive")

    failures: list[str] = []
    if work_dir is None:
        workspace = tempfile.TemporaryDirectory(prefix="ai-token-tracker-scale-")
    else:
        target = Path(work_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=False)
        workspace = contextlib.nullcontext(str(target))
    with workspace as work:
        store = os.path.join(work, "events.jsonl")
        repository = FileRepository(store)

        started = time.perf_counter()
        for offset in range(0, event_count, batch_size):
            limit = min(offset + batch_size, event_count)
            repository.append_many(_event(index) for index in range(offset, limit))
        write_seconds = time.perf_counter() - started

        started = time.perf_counter()
        effective_event_count = 0
        effective_total = 0
        for event in iter_effective_events(repository.iter_events()):
            effective_event_count += 1
            effective_total += event.event_contributing_tokens
        projection_seconds = time.perf_counter() - started

        started = time.perf_counter()
        dashboard = aggregate(store, window="all")
        dashboard_seconds = time.perf_counter() - started
        store_bytes = os.path.getsize(store)
        # Allocation tracing materially distorts JSON parsing and deepcopy timings. Measure
        # latency first, then run the same worst-case dashboard fold once under tracemalloc.
        tracemalloc.start()
        memory_started = time.perf_counter()
        memory_dashboard = aggregate(store, window="all")
        memory_probe_seconds = time.perf_counter() - memory_started
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if memory_dashboard["total_tokens"] != dashboard["total_tokens"]:
            failures.append("instrumented dashboard total differs from timed dashboard total")
    peak_memory_mb = peak_bytes / (1024 * 1024)

    expected_total = event_count * 15
    if effective_event_count != event_count:
        failures.append(f"effective_event_count={effective_event_count} expected={event_count}")
    if effective_total != expected_total or dashboard["total_tokens"] != expected_total:
        failures.append(
            f"total_mismatch projection={effective_total} dashboard={dashboard['total_tokens']} expected={expected_total}"
        )
    if projection_seconds > max_projection_seconds:
        failures.append(f"projection_seconds={projection_seconds:.3f} limit={max_projection_seconds:.3f}")
    if dashboard_seconds > max_dashboard_seconds:
        failures.append(f"dashboard_seconds={dashboard_seconds:.3f} limit={max_dashboard_seconds:.3f}")
    if peak_memory_mb > max_peak_memory_mb:
        failures.append(f"peak_memory_mb={peak_memory_mb:.3f} limit={max_peak_memory_mb:.3f}")

    return ScaleProbeReport(
        generated_at=dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        event_count=event_count,
        effective_event_count=effective_event_count,
        total_tokens=effective_total,
        store_bytes=store_bytes,
        write_seconds=round(write_seconds, 6),
        projection_seconds=round(projection_seconds, 6),
        dashboard_seconds=round(dashboard_seconds, 6),
        memory_probe_seconds=round(memory_probe_seconds, 6),
        peak_memory_mb=round(peak_memory_mb, 3),
        max_projection_seconds=max_projection_seconds,
        max_dashboard_seconds=max_dashboard_seconds,
        max_peak_memory_mb=max_peak_memory_mb,
        passed=not failures,
        failures=failures,
        python=platform.python_version(),
        platform=platform.platform(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate isolated scale evidence for JSONL and dashboard projection")
    parser.add_argument("--events", type=int, default=50_000)
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument("--max-projection-seconds", type=float, default=30.0)
    parser.add_argument("--max-dashboard-seconds", type=float, default=30.0)
    parser.add_argument("--max-peak-memory-mb", type=float, default=512.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        report = run_probe(
            event_count=args.events,
            batch_size=args.batch_size,
            max_projection_seconds=args.max_projection_seconds,
            max_dashboard_seconds=args.max_dashboard_seconds,
            max_peak_memory_mb=args.max_peak_memory_mb,
        )
    except ValueError as exc:
        parser.error(str(exc))
    payload = report.to_dict()
    if args.output:
        _write_atomic(args.output, payload)
    print(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
