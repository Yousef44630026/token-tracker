# Trust Validation

This project treats token usage as an audit problem, not just a counter. The trusted path is:

1. Normalize provider usage into `TokenEvent` and `TokenQuantity`.
2. Derive totals from source-of-truth fields only.
3. Reconcile provider totals when they exist.
4. Mark unknown or unverified fields instead of fabricating confidence.
5. Report provider validation coverage explicitly.

## Trusted Verification Command

Run the focused local suite:

```cmd
scripts\tt-verify.cmd
```

The command runs the core trust tests and prints the provider validation matrix. It does not
make provider calls and it does not use pricing logic.

For the full feature-by-feature QA protocol, see `docs/TRACKER_TEST_GUIDE.md`.

## Provider Matrix

Print the provider validation matrix:

```cmd
scripts\tt-provider-matrix.cmd
```

Write a Markdown artifact:

```cmd
scripts\tt-provider-matrix.cmd --output provider_matrix.md
```

Write JSON:

```cmd
scripts\tt-provider-matrix.cmd --json --output provider_matrix.json
```

Status meanings:

- `pass`: has real fixture coverage and no currently visible validation warning.
- `warn`: has partial coverage, simulated-only coverage, no stream fixture for a streamable surface, or cache coverage that is not real validated.
- `fail`: adapter exists but no mapped realistic fixture proves it.

## Observation Contract

`TokenEvent.observation` remains an extensible dictionary, but operational metrics rely on a
stable subset of fields:

- `status`
- `authoritative`
- `http_status`
- `duration_ms`
- `time_to_first_token_ms`
- `time_to_last_token_ms`
- `provider_request_id`
- `provider_response_id`
- `provider_error_code`
- `retry_count`
- `service_name`
- `tenant_id`
- `cloud_provider`
- `region`
- `deployment`
- `fallback_from`
- `fallback_to`

New code should prefer:

```python
from tracker.observability.observation import build_observation

observation = build_observation(
    status="complete",
    authoritative=True,
    http_status=200,
    duration_ms=123.4,
    service_name="support-api",
    region="francecentral",
)
```

The analytics layer validates the contract without rejecting legacy/custom metadata.

## HTML Report

`tracker.export.html_report.export_html_report(trace, path)` renders a standalone operational
report with:

- readiness overview
- trace summary
- coverage/exactness
- latency
- reliability
- observation-contract summary
- cache efficiency
- RAG efficiency
- agent efficiency
- service attribution
- provider validation matrix
- anomaly signals

The report is derived-only: it does not store new totals and it does not introduce pricing.

## Power BI Integration

For production dashboards, use the Power BI export base:

```cmd
scripts\tt-powerbi-export.cmd --store codex_events.jsonl --output powerbi_dataset
```

It writes import-ready CSV facts/dimensions, `manifest.json`, `measures.dax`, and a local
README. See `docs/POWERBI_INTEGRATION.md`.
