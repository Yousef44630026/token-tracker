"""Derive collector soak evidence from the accumulated health-probe log.

The monitor task appends one probe per minute to collector-health.jsonl. Rather than run a
fresh 72h foreground soak (a laptop sleeps), this reconstructs the same evidence from probes
already recorded: availability, outages, counter monotonicity, and the longest continuous
healthy window. Sleep/off gaps are reported, not counted as outages — a probe that never ran
is not a collector failure.

Usage:
  python scripts/soak_evidence_from_health.py [--health <path>] [--json] [--gap-seconds 180]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


def _parse(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def analyze(path: Path, gap_seconds: float) -> dict:
    probes: list[dict] = []
    malformed = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                probes.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1

    total = len(probes)
    healthy = sum(1 for p in probes if p.get("healthy") is True)
    unhealthy = total - healthy

    parsed = [(_parse(p.get("timestamp", "")), p) for p in probes]
    parsed = [(t, p) for t, p in parsed if t is not None]
    parsed.sort(key=lambda item: item[0])

    # counter monotonicity: events and total must never decrease across time
    regressions = []
    prev_events = prev_total = None
    for _t, p in parsed:
        ev, tot = p.get("events"), p.get("total")
        if isinstance(ev, int) and prev_events is not None and ev < prev_events:
            regressions.append({"at": p.get("timestamp"), "field": "events", "from": prev_events, "to": ev})
        if isinstance(tot, int) and prev_total is not None and tot < prev_total:
            regressions.append({"at": p.get("timestamp"), "field": "total", "from": prev_total, "to": tot})
        if isinstance(ev, int):
            prev_events = ev
        if isinstance(tot, int):
            prev_total = tot

    # gaps (machine off/asleep) vs outages (probe ran, collector unhealthy)
    gaps = []
    outages = 0
    in_outage = False
    longest_healthy = cur_healthy_start = None
    longest_seconds = 0.0
    for i, (t, p) in enumerate(parsed):
        if i > 0:
            delta = (t - parsed[i - 1][0]).total_seconds()
            if delta > gap_seconds:
                gaps.append({"from": parsed[i - 1][1].get("timestamp"), "to": p.get("timestamp"), "seconds": round(delta)})
                cur_healthy_start = None  # a gap breaks a continuous window
        if p.get("healthy") is True:
            if cur_healthy_start is None:
                cur_healthy_start = t
            span = (t - cur_healthy_start).total_seconds()
            if span > longest_seconds:
                longest_seconds = span
                longest_healthy = (cur_healthy_start, t)
            in_outage = False
        else:
            if not in_outage:
                outages += 1
                in_outage = True
            cur_healthy_start = None

    span_start = parsed[0][0].isoformat() if parsed else None
    span_end = parsed[-1][0].isoformat() if parsed else None
    latencies = [p.get("latency_ms") for _, p in parsed if isinstance(p.get("latency_ms"), (int, float))]
    latencies.sort()

    def pct(values: list[float], q: float) -> float | None:
        if not values:
            return None
        idx = min(len(values) - 1, int(q * len(values)))
        return round(values[idx], 1)

    return {
        "health_log": str(path),
        "window_start": span_start,
        "window_end": span_end,
        "probes_total": total,
        "probes_healthy": healthy,
        "probes_unhealthy": unhealthy,
        "malformed_lines": malformed,
        "uptime_ratio_of_probes": round(healthy / total, 6) if total else None,
        "counter_regressions": regressions,
        "outages": outages,
        "sleep_or_off_gaps": len(gaps),
        "longest_gap_seconds": max((g["seconds"] for g in gaps), default=0),
        "longest_continuous_healthy_seconds": round(longest_seconds),
        "longest_continuous_healthy_window": (
            [longest_healthy[0].isoformat(), longest_healthy[1].isoformat()] if longest_healthy else None
        ),
        "latency_ms_p50": pct(latencies, 0.50),
        "latency_ms_p95": pct(latencies, 0.95),
        "gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    default = os.environ.get("TRACKER_HEALTH_LOG", r"C:\ai-token-tracker-data\health\collector-health.jsonl")
    parser.add_argument("--health", default=default)
    parser.add_argument("--gap-seconds", type=float, default=180.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.health)
    if not path.exists():
        print(f"health log not found: {path}")
        return 1
    result = analyze(path, args.gap_seconds)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Collector soak evidence from {result['health_log']}")
    print(f"  window            : {result['window_start']} -> {result['window_end']}")
    print(f"  probes            : {result['probes_total']}  (healthy {result['probes_healthy']}, unhealthy {result['probes_unhealthy']})")
    print(f"  uptime (of probes): {result['uptime_ratio_of_probes']}")
    print(f"  outages           : {result['outages']}")
    print(f"  counter regressions: {len(result['counter_regressions'])}")
    print(f"  sleep/off gaps    : {result['sleep_or_off_gaps']} (longest {result['longest_gap_seconds']}s)")
    h = result["longest_continuous_healthy_seconds"]
    print(f"  longest continuous healthy window: {h}s ({round(h / 3600, 1)}h)")
    print(f"  latency ms p50/p95: {result['latency_ms_p50']} / {result['latency_ms_p95']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
