# Operational release evidence

The strict multi-cloud gate requires current machine-readable evidence in addition to tests,
provider smokes, dashboard coverage, and the synthetic scale probe. Evidence is deliberately
stored outside the repository under `C:\ai-token-tracker-data\evidence`.

## Collector soak

Run the uninterrupted 72-hour probe against the deployed collector:

```powershell
scripts\tt-collector-soak.cmd `
  --store C:\ai-token-tracker-data\collector_events.jsonl `
  --output-dir C:\ai-token-tracker-data\evidence\collector-soak `
  --duration-seconds 259200 `
  --interval-seconds 60
```

The gate requires `passed=true`, at least 72 requested hours, at least 95% wall-clock elapsed
time, 100% observed uptime, complete sampling with no gaps, and an unchanged verified store
prefix. The artifact is bound to the runtime fingerprint that executed the probe.

## Recovery drill

Publish the strict backup, archive, simulated-loss, restore, duplicate-replay, and readability
exercise:

```powershell
python scripts\recovery_drill.py `
  --source C:\ai-token-tracker-data\collector_events.jsonl `
  --evidence-output C:\ai-token-tracker-data\evidence\recovery-drill.json
```

The source ledger is never mutated. The evidence must contain a non-empty snapshot and its
SHA-256, and every required check must pass.

## Billing reconciliation

Cloud billing exports are provider- and contract-specific, so the tracker does not invent a
universal invoice parser. The delivery owner must produce
`C:\ai-token-tracker-data\evidence\billing-reconciliation.json` from the actual external
statement and the exact ledger scope. The gate expects this contract:

```json
{
  "evidence_type": "billing_reconciliation",
  "runtime_fingerprint": "<current 64-character runtime fingerprint>",
  "passed": true,
  "generated_at": "2026-07-22T12:00:00Z",
  "scope_start": "2026-07-01T00:00:00Z",
  "scope_end": "2026-07-22T00:00:00Z",
  "ledger_sha256": "<64-character SHA-256>",
  "external_statement_sha256": "<64-character SHA-256>",
  "absolute_token_variance": 0,
  "token_variance_tolerance": 0,
  "checks": [
    {"name": "external_statement_hashed", "passed": true},
    {"name": "scope_matched", "passed": true},
    {"name": "token_variance_within_tolerance", "passed": true}
  ]
}
```

Use a non-zero tolerance only when the provider statement has a documented aggregation or
rounding rule, and record that rationale in the evidence. Missing or future-dated timestamps,
wrong runtime fingerprints, malformed hashes, incomplete checks, and variance above tolerance
all fail the release gate.
