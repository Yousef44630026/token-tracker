# Archive-First Retention Drill - 2026-07-17

## Scope

- live source: `C:\ai-token-tracker-data\collector_events.jsonl`
- driver: `scripts/recovery_drill.py --json`
- completed: `2026-07-17T14:39:25Z`
- strict snapshot events: **3,714**
- logical snapshot SHA-256: `6d1dc1bb729c6cff8def90f08dd40cc7300de14660a64dd9836333f6aba1ca82`

The live source was never mutated. The drill acquired the repository lock, produced a strict
logical snapshot, and performed archive-first gzip rotation only on a disposable byte copy.
The temporary drill directory was removed automatically after verification.

## Results

| Check | Result | Detail |
|---|---|---|
| Source validation | PASS | Every source row passed strict parsing; no truncated-tail recovery or invalid-row skipping |
| Backup integrity | PASS | Backup SHA-256 matched the strict snapshot |
| Archive-first rotation | PASS | 3,714 events before and after; active JSONL empty; one gzip segment; purge count 0 |
| Canonical accounting | PASS | 1,032,205,653 contributing tokens before and after rotation |
| Compaction readability | PASS | 3,714 events retained and read back |
| Restore integrity | PASS | Restored count and SHA-256 matched the snapshot |
| Duplicate recovery | PASS | Replayed 3,714 events; 0 newly persisted; bytes unchanged |

Overall: **PASS**.

## Live rotation and runtime recovery

After the copy-based drill passed, the same archive-first operator command was run against the
live ledger at `2026-07-17T14:47:44Z`:

- one gzip segment was committed under `collector_events.jsonl.archive`;
- purge count remained **0**;
- the active JSONL was empty after rotation;
- the retention state file was committed atomically.

This exposed useful runtime skew: the already-running collector predated archive-aware reads and
temporarily reported `events=0` and `total=0`. The collector task was restarted and then reported
**3,714 events** and **1,032,205,653 contributing tokens**, exactly matching the pre-rotation
identity. Strict Doctor subsequently passed all 18 checks with zero warnings. A startup source
fingerprint is now emitted by `/healthz`, persisted by the monitor, and compared with the current
source by Doctor so this class of stale-process error fails readiness explicitly.

## Boundary

This proves that the archive-first mechanism preserves identity and canonical accounting both on
a strict disposable snapshot and across one live production rotation. It does not claim that old
archives have been purged, that retention is scheduled automatically, or that an off-host copy
exists.

## Reproduce

```powershell
scripts\_python.cmd scripts\recovery_drill.py `
  --source C:\ai-token-tracker-data\collector_events.jsonl `
  --json
```
