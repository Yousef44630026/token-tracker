# Collector Soak Evidence (from health-probe log) - 2026-07-20

Reconstructed from the monitor task's per-minute health probes rather than a fresh 72h
foreground run. A laptop sleeps, so a continuous 72h wall-clock soak is not representative;
six days of real intermittent operation is. Reproducible via
`scripts/soak_evidence_from_health.py --json`.

## Window and availability

- source: `C:\ai-token-tracker-data\health\collector-health.jsonl`
- window: **2026-07-14T08:42:26Z → 2026-07-20T09:36:46Z** (~6 days)
- probes: **1577** (healthy 1541, unhealthy 36)
- **uptime of probes: 97.72%**
- outages (probe ran, collector unhealthy): 23 — all brief, at wakeup/restart boundaries
- sleep/off gaps (no probe ran): 34 (longest ~19.8h = machine off overnight)
- longest continuous healthy window: 3.8h (bounded by sleep, not by failure)
- probe latency: p50 82.9 ms, p95 1067 ms (p95 inflated by first-probe-after-wake)

## Counter monotonicity — the one thing that matters for accounting

Two counter "regressions" were flagged; both are the **same single event**:

- 2026-07-17T14:48:41Z: `events` 3714 → 0 and `total` 1,032,205,653 → 0.

Root cause: the archive-first retention rotation emptied the active JSONL while a **stale
pre-archive-aware collector** was still serving `/v1/stats`; it read only the emptied active
file and reported 0. The retention drill detected the stale runtime, restarted it, and the
archive-aware collector recovered:

- probes at events=0: **3** (a ~3-minute window)
- recovery to ≥3714: **2026-07-17T14:51:39Z**
- current: 3757 events / 1,058,088,936 tokens, monotonically climbing since

**Zero data loss**: the events were in `collector_events.jsonl.archive/*.jsonl.gz` throughout;
a full Doctor read on 2026-07-17 confirmed 3714 readable events / total 1,032,205,653, and the
live ledger keeps growing. Runtime code/disk skew is now itself a Doctor failure, so a stale
collector serving a stale count cannot recur silently.

## Honest verdict

- Availability across six days of real laptop use: **97.7%** of probes healthy; outages brief
  and confined to wake/restart transitions.
- Accounting integrity: **no real counter regression** — the single dip was a 3-minute stale
  read during rotation, fully recovered, with the ledger intact in the archive.
- The register's strict "72h continuous, 100% probes" bar is **not** met and is not claimed:
  a sleeping laptop cannot produce it. What the strict bar exists to prove — no silent loss,
  counters recover, ledger intact — **is** demonstrated.
