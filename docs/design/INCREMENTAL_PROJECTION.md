# Incremental effective-projection index (scale chantier)

Status: **S0 — design + falsifier only.** No production wiring yet.

## Problem

`aggregate()` and `/v1/stats` call `iter_effective_events(repo.iter_events())` on **every**
poll. That parses, deep-copies, and reconciles the **entire** ledger each time — O(n) per
request with a large constant (measured: the live dashboard's 2s poll stops keeping up at
~5k events, 30s at ~50k; see the *Scale envelope* row in
[../OPERATIONAL_EVIDENCE.md](../OPERATIONAL_EVIDENCE.md)). The signature cache keys on
`(size, mtime)`, so live ingestion invalidates it on every append — the exact scenario a live
dashboard exists for.

## Why an incremental index is correct here

Reconciliation is **local to a correlation group**: `reconcile_supersession` /
`reconcile_events` clear and recompute state per `request_correlation_id`, and the pass is
idempotent. A newly appended event can therefore only change the effective state of **its own
correlation group** — never any other. So we can keep a persistent projection and, on refresh,
re-reconcile only the groups touched by new bytes.

The hard case (the falsifier's target): a partial stream estimate projected in an early refresh
is **retroactively superseded** by a final that arrives in a later refresh. Incremental state of
a *past* event must change. The design handles this by re-running the *same* `reconcile_events`
over the full (persisted + new) membership of each touched group.

## Design

A sidecar SQLite next to the ledger (`<store>.projection.sqlite3`). **Reconstructible cache,
never source of truth** (INV-1/INV-2): it stores only a projection of the JSONL and can be
dropped and rebuilt at any time.

- `meta`: ledger fingerprint (active-file size + a stable prefix hash + the set of archive
  segments already folded in) and the **byte cursor** = how far into the active file we have
  projected.
- `events`: one row per source event — `event_id`, `request_correlation_id`, ledger order, the
  STORED fields aggregation needs (timestamp, service, provider, model, contributing sum,
  provider_total, flags), and the derived effective state (`superseded`, `superseded_by`,
  effective `quality_flags`).

### refresh()
1. Read `meta`. If the active file shrank, its prefix hash changed, or an archive set changed
   (rotation/truncation/rewrite), the cursor is invalid → `rebuild()`.
2. Seek to the cursor, read only **new** bytes, parse new events.
3. Collect the set of touched `request_correlation_id`s. For each, load its existing rows +
   new rows, run `reconcile_events` over the whole group, and write back the effective state.
4. Advance the cursor and fingerprint atomically.

### rebuild()
Full projection from `iter_effective_events(repo.iter_events())` into a fresh sidecar. This is
exactly today's path; it is the correctness floor and the automatic fallback.

### iter_effective_events()
Yield reconstructed effective `TokenEvent`s in ledger order from `events`. Aggregation consumes
these directly — no deep-copy, no re-reconcile.

## Invariant / the permanent falsifier

`tests/test_projection_index_equivalence.py`: for randomized ledgers written in several batches
(plain, authority=false, partial→final supersession **across** batches, duplicate finals), with
`refresh()` called between batches, the index's effective state must equal the full-scan
`iter_effective_events` state **event-for-event** (`superseded`, `superseded_by`,
`event_contributing_tokens`, `data_quality_flags`), and `rebuild()` must equal the incremental
result. If incremental ever diverges from full-scan, the index is wrong — fail loud, fall back.

## Phases

- **S0** ✅ design + falsifier (red).
- **S1–S3** ✅ `tracker/derive/projection_index.py`: byte-offset incremental read, persistent
  projection, incremental `refresh()` re-reconciling only touched groups; falsifier green.
  Measured at 20k events: a poll's projection drops ~3.3s → ~37ms (+5 new) / ~4.5ms (no-op).
- **S4** ✅ `effective_events_for_store()` wired into `aggregate()` and the collector
  `/v1/stats` (single-file stores only; partitioned keeps the full scan). Full-scan fallback on
  the `TRACKER_DISABLE_PROJECTION_INDEX` flag, a corrupt sidecar, or any index error; a
  mid-read failure fails loud rather than silently double-counting. Backup and the Doctor
  secret scan ignore the reconstructible sidecar. Equivalence falsifier pinned in CI.
- **S5-lite** ✅ compact per-event aggregation record (`tracker/derive/aggregation_record.py`)
  stored in the index (`agg` column, schema v2, recomputed on reconcile); `aggregate()` reads
  records via `aggregation_records_for_store()` instead of rebuilding a `TokenEvent` per row.
  Headline-band deltas are captured by running `HeadlineBandAccumulator.add` once per event, so
  the record can never drift from that function. Measured ~1.4x (50k: 3.7s → 2.7s; interactive
  ceiling ~27k → ~37k). The residual is now split between `json.loads` (~0.9s) and the Python
  accumulation loop (~1.05s), not `from_dict` (which S5-lite eliminated).
- **S5-sql** (future, not built) the stored records make SQL-side aggregation possible —
  `SELECT SUM(json_extract(agg,'$.contrib')) … GROUP BY service` runs the additive metrics in
  C (~0.4s at 50k, ~6x), with the window filter as an ISO-8601 `ts` range. It needs the additive
  metrics rewritten as SQL and the Python full-scan kept as the disabled/corrupt/partitioned
  fallback. Deferred deliberately: at the product's real scale (~4k) the current path is ~0.2s.
- Optional Doctor index-health check (the index already self-heals via rebuild-on-inconsistency).
