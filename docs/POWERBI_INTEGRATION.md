# Power BI Integration

The recommended BI target is Power BI. It handles refreshes, relationships, trend pages,
KPI cards, and service/provider slicing better than a manually maintained Excel workbook.

The export still uses CSV files, so Excel can open the same data when someone needs a quick
inspection path.

## Export Command

From a recorded JSONL event store:

```cmd
scripts\tt-powerbi-export.cmd --store codex_events.jsonl --output powerbi_dataset
```

Or through Python:

```cmd
python -m tracker.proxy.cli powerbi-export --store codex_events.jsonl --output powerbi_dataset
```

The output folder contains:

- `fact_token_events.csv`
- `fact_token_quantities.csv`
- `fact_spans.csv`
- `fact_service_daily.csv`
- `dim_service.csv`
- `dim_model.csv`
- `dim_provider_surface.csv`
- `dim_token_type.csv`
- `metric_snapshots.csv`
- `provider_validation_matrix.csv`
- `data_dictionary.csv`
- `manifest.json`
- `measures.dax`
- `README.md`

## Why Power BI

Power BI is the better base for production metrics because the project needs:

- scheduled refresh
- service/provider/model slicing
- latency and reliability trends
- validation-readiness dashboards
- metric cards for cache, RAG, and agent behavior
- separate fact grains without accidental double counting

Excel remains useful for local audits, but Power BI is the right operational surface.

## Counting Rules

Use only one grain at a time:

- Event grain total: `fact_token_events[event_contributing_tokens]`
- Quantity grain total: `fact_token_quantities[quantity_in_total]`

Do not sum:

- `provider_total_tokens`
- raw `quantity`

Do not add event-grain totals and quantity-grain totals together. They are two views of the
same usage.

Cache, reasoning, and thinking fields are useful diagnostics, but they are not added twice
into total contributing tokens.

## First Dashboard Pages

Recommended pages:

1. Executive usage: total tokens, event count, success rate, p95 latency, flagged events.
2. Service attribution: service, tenant, cloud, region, provider, model, deployment.
3. Reliability: errors, rate limits, retries, provider-total mismatches.
4. Cache efficiency: cache-read tokens and cache hit rate by provider/model/service.
5. RAG and agent efficiency: use `metric_snapshots` and `fact_spans` when spans are exported.
6. Trust readiness: provider validation status and remaining fixture gaps.

## Base DAX Measures

The exporter writes `measures.dax`. Start with those measures:

- Total Contributing Tokens
- Input Tokens
- Output Tokens
- Cached Input Tokens
- Cache Hit Rate
- Total Events
- Success Rate
- Error Rate
- Rate Limited Events
- Retry Count
- Flagged Events
- Provider Mismatch Events
- Average Duration MS
- P95 Duration MS
- Average TTFT MS

## Refresh Strategy

Recommended production shape:

1. The tracker writes JSONL events continuously.
2. A scheduled job runs `powerbi-export` into a stable folder.
3. Power BI imports that folder and refreshes on schedule.
4. The report uses `manifest.json` and `provider_validation_matrix.csv` to show whether the
   dashboard is fully real-validated or still warning-only.

No pricing or cost fields are exported.
