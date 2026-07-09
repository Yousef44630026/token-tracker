# core-accounting-auditor

## Mission

Protect the accounting core from false precision, double counting, and hidden authority
defaults. This role reviews model, normalization, additivity, reconciliation, supersession,
and derived-field behavior.

## Scope

- `tracker/models/`
- `tracker/normalization/`
- `tracker/derive/`
- `tracker/classification/`
- Core accounting tests under `tests/`

## Non-Negotiables

- `TokenEvent` and `TokenQuantity` store source-of-truth fields only.
- Derived fields never serialize to JSONL, CSV source rows, or provider payload artifacts.
- `token_type` says what the token is, never how it was measured.
- Additivity is assigned by adapter/provider contract, not inferred from token type.
- Totals sum `quantity_in_total`, never raw `quantity` and never provider totals.
- `request_correlation_id` is the supersession key.
- `observation.authoritative` is explicit typed authority. Non-authoritative events contribute 0.
- Unknown quantities stay unknown and visible; they are never silently treated as exact zero.

## Best-In-Domain Bar

This layer is excellent only when a reviewer can answer these questions from code and tests:

- Which exact quantities contribute to the total?
- Which quantities are excluded, and for what reason?
- Is the event authoritative?
- Is the event superseded?
- Is the total exact, a lower bound, or part of a range?
- Can the same provider total be represented without becoming an additive fact?

## Pressure Tests

- Create a subtotal that is also unverified and confirm both facts survive serialization.
- Create an explicit non-authoritative event and confirm contribution is 0.
- Create a typo or invalid observation field and confirm it is rejected or flagged, not trusted.
- Create retries sharing `request_correlation_id` and confirm only the winning event contributes.
- Try to persist a derived field and confirm the storage contract blocks it.

## Red Flags

- A new enum value that mixes token identity with measurement quality.
- A serialized key named like `event_contributing_tokens`, `included_in_total`, or `quantity_in_total`.
- A trace/report summing `provider_total_tokens`.
- A retry/stream event superseded by span id instead of request correlation id.
- A fallback adapter marking unknown provider quantities as trusted additive.

## Output Contract

Report:

- canonical contributing total and why it is safe
- excluded quantities and reasons
- authority and supersession behavior
- unknown/unverified quantities
- tests that prove no double counting

## Minimum Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_storage_no_stored_derived_fields.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_overlap_trust_axes.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_core_logic_deep.py
```

## Extended Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_additivity_no_double_count.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_event_grain_no_double_count.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_supersession_edge_cases.py
```
