# Operational Evidence Register

This register separates code-level correctness from claims that require external evidence.
A green unit-test suite must not promote an unobserved provider or workload to "operational".

| Evidence | Current state | Pass criterion | Artifact |
|---|---|---|---|
| Permanent accounting falsifiers | Automated | Six named invariant tests pass in GitHub Actions | CI run URL and commit SHA |
| Provider payload semantics | Partial | Real redacted capture for every supported surface and usage mode | `fixtures/providers/manifest.json` entry |
| Billing reconciliation | Not demonstrated | Tracker totals reconciled to a provider invoice for a fixed window | Signed reconciliation summary |
| Proxy soak | Not demonstrated | 72 hours under representative streaming load with bounded memory/handles and zero silent loss | Soak report plus event store hash |
| Collector supervision | Installable, drill pending | Auto-start, restart-on-failure, and downtime alert verified | `scripts/tt-collector-task.ps1` status plus incident drill |
| Storage substrate | Unsafe when under sync | Live ledger resides on a non-synced local volume; exports may be synced | `tt-doctor --strict-warnings` output |
| Claude transcript importer | Canary implemented | Import report has no format-drift warnings and expected extraction ratio | `ClaudeImportReport` JSON |
| Estimator quality | Backend disclosed | `tiktoken` active or fallback explicitly accepted; error distribution measured by content class | Doctor output and estimate-vs-provider report |
| Dashboard consumption | Not demonstrated | Scheduled export refreshes a connected dashboard and freshness is monitored | Refresh history and dashboard URL |
| Retention and recovery | Not demonstrated | Rotation, backup, restore, and duplicate-recovery drill pass | Recovery drill report |

## Release Rule

Code readiness and operational readiness are separate statuses. A release may claim the
accounting core is validated when CI passes. It may claim a provider surface is operational
only when its real fixture, soak/reliability evidence, and storage path are all present.

## Immediate Operator Commands

```powershell
scripts\tt-doctor.cmd --store C:\ai-token-tracker-data\collector_events.jsonl --strict-warnings
scripts\tt-check.cmd
scripts\tt-verify.cmd
```

Do not store credentials, raw prompts, invoice line items, or unredacted provider responses
in this register. Link only to approved redacted artifacts.
