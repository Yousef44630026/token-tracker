"""Long-running collector availability and append-only integrity evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from tracker.ops.collector_monitor import check_collector

Probe = Callable[..., dict[str, Any]]


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot(path: str, *, prefix_bytes: int | None = None) -> dict[str, Any]:
    target = os.path.abspath(path)
    if not os.path.exists(target):
        return {"path": target, "exists": False, "size_bytes": 0, "prefix_bytes": 0, "sha256": None}

    stat = os.stat(target)
    limit = stat.st_size if prefix_bytes is None else min(prefix_bytes, stat.st_size)
    digest = hashlib.sha256()
    remaining = limit
    with open(target, "rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return {
        "path": target,
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "prefix_bytes": limit - remaining,
        "sha256": digest.hexdigest(),
    }


def _safe_snapshot(path: str, *, prefix_bytes: int | None = None) -> dict[str, Any]:
    try:
        return _snapshot(path, prefix_bytes=prefix_bytes)
    except OSError as exc:
        return {
            "path": os.path.abspath(path),
            "exists": None,
            "size_bytes": None,
            "prefix_bytes": 0,
            "sha256": None,
            "error_type": type(exc).__name__,
        }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _write_json(path: str, payload: Mapping[str, Any], *, durable: bool) -> None:
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = f"{target}.tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(dict(payload), handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        if durable:
            os.fsync(handle.fileno())
    os.replace(temporary, target)


def run_soak(
    *,
    base_url: str,
    store_path: str,
    output_dir: str,
    duration_seconds: float,
    interval_seconds: float,
    auth_token: str | None = None,
    timeout: float = 3.0,
    max_samples: int | None = None,
    durable: bool = True,
    probe: Probe = check_collector,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run bounded probes and write a single audit-friendly soak summary."""
    if duration_seconds < 0:
        raise ValueError("duration_seconds must be non-negative")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if max_samples is not None and max_samples <= 0:
        raise ValueError("max_samples must be positive")

    output_dir = os.path.abspath(output_dir)
    health_log = os.path.join(output_dir, "samples.jsonl")
    alert_log = os.path.join(output_dir, "alerts.jsonl")
    summary_path = os.path.join(output_dir, "summary.json")
    started_at = _timestamp()
    started = monotonic()
    start_store = _safe_snapshot(store_path)
    start_prefix_bytes = int(start_store.get("size_bytes") or 0)

    samples = 0
    healthy_samples = 0
    failed_samples = 0
    outage_count = 0
    consecutive_failures = 0
    max_consecutive_failures = 0
    counter_regressions = 0
    latencies: list[float] = []
    first_events: int | None = None
    final_events: int | None = None
    first_total: int | None = None
    final_total: int | None = None
    stop_reason = "duration_elapsed"
    interrupted = False

    try:
        while True:
            sample = probe(
                base_url=base_url,
                health_log=health_log,
                alert_log=alert_log,
                auth_token=auth_token,
                timeout=timeout,
                durable=durable,
            )
            samples += 1
            latencies.append(float(sample.get("latency_ms", 0.0)))
            healthy = sample.get("healthy") is True
            if healthy:
                healthy_samples += 1
                consecutive_failures = 0
            else:
                failed_samples += 1
                if consecutive_failures == 0:
                    outage_count += 1
                consecutive_failures += 1
                max_consecutive_failures = max(max_consecutive_failures, consecutive_failures)

            events = sample.get("events")
            total = sample.get("total")
            if isinstance(events, int):
                if first_events is None:
                    first_events = events
                if final_events is not None and events < final_events:
                    counter_regressions += 1
                final_events = events
            if isinstance(total, int):
                if first_total is None:
                    first_total = total
                if final_total is not None and total < final_total:
                    counter_regressions += 1
                final_total = total

            if max_samples is not None and samples >= max_samples:
                stop_reason = "max_samples"
                break
            elapsed = monotonic() - started
            if elapsed >= duration_seconds:
                break
            sleep(min(interval_seconds, max(0.0, duration_seconds - elapsed)))
    except KeyboardInterrupt:
        interrupted = True
        stop_reason = "interrupted"

    elapsed_seconds = round(monotonic() - started, 3)
    end_store = _safe_snapshot(store_path, prefix_bytes=start_prefix_bytes)
    prefix_unchanged = bool(
        start_store.get("exists") is True
        and end_store.get("exists") is True
        and end_store.get("size_bytes", 0) >= start_prefix_bytes
        and end_store.get("prefix_bytes") == start_prefix_bytes
        and end_store.get("sha256") == start_store.get("sha256")
    )
    store_verified = prefix_unchanged and "error_type" not in start_store and "error_type" not in end_store
    passed = not interrupted and failed_samples == 0 and counter_regressions == 0 and store_verified
    summary: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started_at,
        "ended_at": _timestamp(),
        "base_url": base_url.rstrip("/"),
        "requested_duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed_seconds,
        "interval_seconds": interval_seconds,
        "stop_reason": stop_reason,
        "interrupted": interrupted,
        "samples": samples,
        "healthy_samples": healthy_samples,
        "failed_samples": failed_samples,
        "uptime_ratio": round(healthy_samples / samples, 6) if samples else 0.0,
        "outage_count": outage_count,
        "max_consecutive_failures": max_consecutive_failures,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "collector_counters": {
            "first_events": first_events,
            "final_events": final_events,
            "first_total": first_total,
            "final_total": final_total,
            "regressions": counter_regressions,
        },
        "store_integrity": {
            "prefix_unchanged": prefix_unchanged,
            "verified": store_verified,
            "start": start_store,
            "end": end_store,
        },
        "artifacts": {"samples": health_log, "alerts": alert_log, "summary": summary_path},
        "passed": passed,
    }
    _write_json(summary_path, summary, durable=durable)
    return summary


def _default_output_dir(store_path: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    return os.path.join(os.path.dirname(os.path.abspath(store_path)), "soak", stamp)


def main(argv: list[str] | None = None) -> None:
    environment = os.environ
    host = environment.get("TRACKER_HOST", "127.0.0.1")
    port = environment.get("TRACKER_PORT", "8787")
    store = environment.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl")
    parser = argparse.ArgumentParser(description="Run a collector availability and store-integrity soak")
    parser.add_argument("--base-url", default=environment.get("TRACKER_MONITOR_URL", f"http://{host}:{port}"))
    parser.add_argument("--store", default=store)
    parser.add_argument("--output-dir")
    parser.add_argument("--duration-seconds", type=float, default=72 * 60 * 60)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--no-durable", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = run_soak(
        base_url=args.base_url,
        store_path=args.store,
        output_dir=args.output_dir or _default_output_dir(args.store),
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        max_samples=args.max_samples,
        timeout=args.timeout,
        auth_token=environment.get("TRACKER_AUTH_TOKEN"),
        durable=not args.no_durable,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    else:
        print(
            "collector soak: "
            f"passed={summary['passed']} samples={summary['samples']} "
            f"uptime={summary['uptime_ratio']:.2%} outages={summary['outage_count']}"
        )
        print(f"artifacts: {summary['artifacts']['summary']}")
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
