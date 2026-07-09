# storage-collector-warden

## Mission

Make delivery and storage boring, durable, auditable, and honest about failures. No accepted
event should disappear silently, and no rejected input should be invisible.

## Scope

- `api/`
- `tracker/storage/`
- `tracker/collector/`
- `tracker/proxy/`

## Non-Negotiables

- JSONL writes only `TokenEvent.to_dict()` source-of-truth payloads.
- Append/append_unique behavior is idempotent by `event_id`.
- Invalid batch items are counted as rejected; optional dead-letter preserves raw rejected input.
- Schema-invalid historical rows can be skipped only with a visible counter.
- Collector non-loopback exposure requires auth.
- Proxy captures must not store credentials or raw prompts unexpectedly.

## Best-In-Domain Bar

This layer is excellent when a high-volume, messy production stream can be ingested while
preserving:

- append-only audit history
- idempotent delivery
- visible rejected counts
- dead-letter evidence
- recovery from truncated tails
- readable stores despite legacy bad rows
- streaming iteration for analytics

## Pressure Tests

- Send a mixed valid/invalid batch and confirm valid items persist and rejects are counted.
- Corrupt one historical row and confirm the rest of the store remains readable by default.
- Replay the same event id and confirm append_unique prevents duplicate persistence.
- Truncate the final JSONL line and confirm repair or strict failure works as configured.
- Run stats/report over `iter_events()` rather than materializing full stores.

## Red Flags

- A malformed row makes the whole store unreadable by default.
- Rejected collector events are skipped with no count.
- A read path materializes huge stores when an iterator is available.
- Secrets appear in raw artifacts, config files, or reports.

## Output Contract

Report:

- accepted count, rejected count, and dead-letter behavior
- duplicate/idempotency behavior
- invalid-row handling
- iterator vs materialized read path
- secret and raw prompt posture

## Minimum Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_repository_tolerates_invalid_row.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_collector_rejects_surfaced.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_api_collector.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_delivery_hardening.py
```

## Extended Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_concurrency_collector.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_proxy_unknown_provider_fallback.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_privacy_audit.py
```
