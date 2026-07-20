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

Require a specific REAL proof as a release precondition:

```cmd
scripts\tt-provider-matrix.cmd --require-proven azure_openai:chat_completions:stream
scripts\tt-provider-matrix.cmd --require-proven vertex_ai:embeddings:usage
```

The second command intentionally exits non-zero until a real Vertex embeddings fixture exists.
Requirements use `provider:surface[:capability]`; shared adapter code never satisfies a different
cloud's wire-format proof.

Status meanings:

- `pass`: has real fixture coverage and no currently visible validation warning.
- `warn`: has partial coverage, simulated-only coverage, no stream fixture for a streamable surface, or cache coverage that is not real validated.
- `fail`: adapter exists but no mapped realistic fixture proves it.

Capability certification uses stricter evidence labels:

- `proven`: at least one mapped REAL fixture exercised that exact capability.
- `simulated`: only synthetic fixtures exercise it.
- `unvalidated`: implemented or declared, but no mapped fixture exercises it.
- `unsupported`: an explicit provider/product boundary, such as Cohere Embed on Bedrock not
  returning a response token count.

Run `scripts\tt-release-gate.cmd` before delivery. It combines the complete code gate, operational
Doctor, provider capability proof, dashboard freshness, quality status, and coverage thresholds.

## Observation Contract

`TokenEvent.observation` is a typed `Observation` with a real `authoritative: bool` field.
It retains a mapping interface and extensible provider metadata, while known operational
fields are validated atomically:

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

Missing authority fails closed for legacy reads and is rejected by live v9 ingestion.
Custom metadata remains accepted; known fields cannot be mutated into invalid values.

## Canonical Trust Band

Every trace reports `[headline_floor_tokens, headline_estimate_tokens,
headline_ceiling_tokens]`. A `null` ceiling means the upper bound is open, never zero.
Provider totals pin the event band when present; signed under/over attribution remains visible
separately. `capture_completeness_ratio` is `null` when the ceiling is open or attribution is
over/mixed, because a precise-looking percentage would be misleading.

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
