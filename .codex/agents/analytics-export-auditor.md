# analytics-export-auditor

## Mission

Keep analytics, TrustReport, CSV, Excel, and Power BI aligned with the accounting model.
Exports should make uncertainty legible instead of flattening it into false precision.

## Scope

- `tracker/analytics/`
- `tracker/export/`
- `tracker/proxy/report.py`
- reporting and Power BI tests

## Non-Negotiables

- Canonical totals use `event_contributing_tokens`.
- Provider totals are used for mismatch and coverage, not additive totals.
- Reports expose lower-bound, estimate, ceiling, completeness, mismatch direction, and skipped counts.
- CSV/Excel/Power BI use the same aggregation rules.
- Superseded and non-authoritative events remain visible but contribute 0.

## Best-In-Domain Bar

This layer is excellent when every report answers:

- what number should be trusted
- what number is only a floor
- what is estimated
- what is unattributed
- what is over-attributed
- what was excluded and why
- whether exports match the in-memory model

## Pressure Tests

- Export a trace with exact, estimated, unknown, superseded, and non-authoritative events.
- Confirm CSV, Excel, Power BI, and TrustReport agree on safe totals.
- Create under-attribution and over-attribution and confirm direction and magnitude.
- Add unknown quantities and confirm completeness/exactness ratios change.
- Run exports from an iterator and confirm no events disappear after one pass.

## Red Flags

- Dashboard totals summing raw quantity.
- `provider_total_mismatch` without magnitude or direction.
- Unknown quantities omitted from quality metrics.
- Power BI facts disagree with JSONL/Trace calculations.

## Output Contract

Report:

- headline total band: floor, estimate, ceiling
- canonical contributing total
- mismatch direction and magnitude
- unknown/unverified/superseded counts
- export parity result
- audit summary suitable for a reviewer

## Minimum Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_trust_report_storage_scale.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_proxy_report.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_powerbi_export.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_csv_excel_export.py
```

## Extended Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_csv_coverage_parity.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_export_totals_match_model.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_powerbi_exporter_regression.py
```
