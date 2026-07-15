# Retention and Recovery Drill - 2026-07-15

## Scope

- live store under test: `C:\ai-token-tracker-data\collector_events.jsonl`
- driver: `scripts/recovery_drill.py` (reproducible; JSON summary with `--json`)
- ledger contents at drill time: **3364 real Claude Code events**, baseline
  SHA-256 prefix `adc78a4a1d9c...`

The live ledger was never mutated. Every operation ran on copies made from a
lock-consistent snapshot (`FileRepository.write_compacted` holds the store lock while it
copies), so a concurrently-writing collector cannot produce a torn snapshot. The live
`/v1/stats` event count was `3364` immediately before and after the drill.

## Passing Checks

| Check | Result | Detail |
|---|---|---|
| snapshot | PASS | 3364 events captured, sha `adc78a4a1d9c` |
| backup_integrity | PASS | backup SHA-256 == snapshot SHA-256 |
| rotation_compaction | PASS | compacted copy kept 3364, read back 3364 |
| simulated_loss | PASS | working copy deleted |
| restore_integrity | PASS | restored 3364 events, SHA-256 matches baseline |
| duplicate_recovery | PASS | re-appended all 3364, **0** newly persisted, store byte-identical |
| readability | PASS | full streaming read returned 3364 events |

Overall: **PASS**.

## What This Proves And What It Does Not

- Proven: a backup is byte-faithful; compaction/rotation preserves every live event and
  stays readable; a lost store is fully recoverable from backup with an identical hash;
  re-ingesting a whole store persists no duplicates (dedup by deterministic `event_id`);
  the restored store streams end to end.
- Not proven here: an off-host/offsite backup rotation schedule, and recovery from a
  crash-truncated final line under real power loss (the tail-repair path exists in
  `FileRepository` and is covered by unit tests, but was not exercised as a physical
  drill). A scheduled backup copy to a separate volume remains an operator task.

## Reproduce

```powershell
python scripts\recovery_drill.py --source C:\ai-token-tracker-data\collector_events.jsonl
```
