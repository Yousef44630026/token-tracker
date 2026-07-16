# Strict Retention and Recovery Drill - 2026-07-16

## Scope

- live store: `C:\ai-token-tracker-data\collector_events.jsonl`
- driver: `scripts/recovery_drill.py --json`
- ledger at snapshot: **3586 events**
- logical snapshot SHA-256: `0ea4cbcd20d7fb7ead4bd6a0c968b91a4f37bfc2b57de4f5345567d73610f572`
- completed at: `2026-07-16T08:54:15Z`

The source was never mutated. The repository lock produced a consistent logical snapshot,
and the source reader ran with both truncated-tail recovery and invalid-row skipping disabled.
The drill therefore fails before making backup claims if any source row is malformed,
truncated, or schema-invalid.

## Results

| Check | Result | Detail |
|---|---|---|
| source_validation | PASS | strict read accepted every source row |
| snapshot | PASS | 3586 events; SHA prefix `0ea4cbcd20d7` |
| backup_integrity | PASS | backup hash equals snapshot hash |
| rotation_compaction | PASS | kept 3586; strict read-back returned 3586 |
| simulated_loss | PASS | disposable primary copy deleted |
| restore_integrity | PASS | restored 3586 events with matching hash |
| duplicate_recovery | PASS | replayed 3586; newly persisted 0; bytes unchanged |
| readability | PASS | strict streaming read returned 3586 events |

Overall: **PASS**.

## Boundaries

This proves strict logical readability, local backup integrity, compaction, restoration, and
idempotent replay on the live ledger at the stated instant. It does not prove an off-host
backup schedule, physical media failure recovery, or disaster recovery on another machine.

## Reproduce

```powershell
scripts\_python.cmd scripts\recovery_drill.py `
  --source C:\ai-token-tracker-data\collector_events.jsonl `
  --json
```
