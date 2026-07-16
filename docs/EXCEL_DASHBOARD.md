# Excel Dashboard Reporting

The Excel dashboard is an optional presentation layer. JSONL remains the source of truth;
cost, safe totals, request counts, latency statistics, and chart tables are derived only in
memory and in the generated workbook.

The workbook opens on an interactive Azure/Foundry-style `Dashboard`. Provider, model,
deployment, environment, use case, and date selectors recalculate the KPI cards and four native
charts without macros. `Data`, `Coûts`, `Tokens & Latence`, and `Use cases` remain deterministic
audit views. A `veryHidden` `_Lists` sheet holds bounded dropdown sources; it is not reporting data.

## Stored v8 event schema

`TokenEvent.to_dict()` writes these fields and no derived totals:

| Field | Type |
|---|---|
| `event_id` | non-empty string |
| `request_correlation_id` | non-empty string; one provider-call attempt |
| `trace_id`, `span_id` | non-empty string |
| `parent_span_id`, `business_id`, `workflow`, `environment` | string or null |
| `provider`, `model`, `api_surface` | string or null |
| `quantities` | list of `TokenQuantity` objects |
| `provider_total_tokens` | non-negative integer or null; raw event-level value, never summed across event rows |
| `superseded` | boolean |
| `superseded_by` | event id or null |
| `data_quality_flags` | list of bounded strings |
| `request_hash`, `response_hash` | string or null |
| `timestamp` | ISO-8601 string or null |
| `observation` | validated object with explicit `authoritative` boolean |

Each stored quantity contains:

| Field | Type / values |
|---|---|
| `token_type` | semantic enum: `input`, `output`, `cached_input`, `cache_creation_input`, `reasoning`, `thinking`, `embedding`, `rerank_input`, `rerank_output`, `audio_input`, `audio_output`, `image_input`, `video_input` |
| `quantity` | non-negative integer or null |
| `precision_level` | `exact`, `estimate`, `unknown` |
| `usage_source` | provider/local/stream provenance enum |
| `additivity` | compatibility enum: `total_contributing`, `subtotal_of`, `unverified` |
| `overlap` | `independent` or `subtotal_of` |
| `trust` | `verified` or `unverified` |
| `aggregation_mode` | `sum` |
| `token_role`, `subtotal_of` | string or null |
| `unknown_reason` | enum value or null |
| `metadata` | object |

Known observation fields are `authoritative`, `status`, `http_status`, `duration_ms`,
`time_to_first_token_ms`, `time_to_last_token_ms`, provider request/response/error ids,
`retry_count`, service/tenant/cloud/region/deployment labels, and fallback endpoints. Extra
validated metadata remains inside the same object.

## Reporting rules

- Duplicate `event_id` rows are reduced deterministically to the latest timestamp (then file
  order). This protects multi-file exports from at-least-once copies.
- Request supersession uses the core reconciler, not a blind `drop_duplicates`: final provider
  usage supersedes partial stream estimates and duplicate finals. Correlation-id collisions
  stay visible through `correlation_id_collision`.
- `quantity_in_total` is the only summable quantity-grain token field.
- `event_contributing_tokens_once` appears on one quantity row per event, so event totals are
  not repeated.
- `request_count_once` and `request_latency_ms` appear on one latest authoritative row per
  `request_correlation_id`. Average and p95 latency therefore weight requests equally.
- `event_count_once`, latency observation counters, cache-read tokens, and pricing-coverage
  numerators/denominators are workbook-only additive measures. They simplify Excel formulas and
  are never written back to `TokenEvent`.
- Superseded events have their own additive KPI. Unknown, mismatch, and flagged-event KPI count
  only active authoritative observations, so an expected partial-stream replacement is visible
  without being misclassified as a current data-quality failure.
- Cost is never stored. For a subtotal such as `cached_input` inside `input`, billing tokens
  are allocated as `(input - cached_input)` at the input price plus `cached_input` at its own
  price. This avoids charging the same token twice.
- Missing quantity, price, or inconsistent subtotal allocation produces a blank cost and a
  `cost_quality` reason. Unknown never becomes zero.
- Multiple currencies are rejected rather than silently summed.

## Price table

Copy `examples/model_prices.template.csv` to `prices.csv`, then add effective-dated prices:

```csv
provider,model,token_type,price_per_million_tokens,currency,effective_from,effective_to
azure_openai,YOUR_MODEL,input,YOUR_PRICE,USD,2026-01-01,
azure_openai,YOUR_MODEL,cached_input,YOUR_PRICE,USD,2026-01-01,
azure_openai,YOUR_MODEL,output,YOUR_PRICE,USD,2026-01-01,
```

`provider`, `model`, and `token_type` accept `*` as a fallback. Exact matches win. Prices are
external because Azure/Foundry rates can vary by region, deployment type, date, and contract.

## Run

```powershell
python -m pip install -e ".[reporting]"
scripts\tt-dashboard.cmd --data-dir .\data --prices .\prices.csv --output .\dashboard.xlsx
```

Add `--recursive` only for a partitioned event store. Malformed and schema-invalid JSONL rows
are logged by path and line number, without logging their potentially sensitive content.

The workbook is regenerated from scratch and contains five visible sheets: `Data`, `Dashboard`,
`Coûts`, `Tokens & Latence`, and `Use cases`. Dashboard formulas use the `DataTable` Excel table,
so dropdown and date changes update KPI and chart values. Exact interactive P95 uses the Microsoft
365 `FILTER` function; other KPI formulas use broadly supported `SUMIFS` arithmetic.

The interactive chart layer is bounded to the latest 730 observed dates and the 20 leading models
to cap workbook size and recalculation cost. KPI cards cover the full selected in-workbook range.
Rerun the command after changing JSONL or prices so categories and dropdown values are rebuilt.
Replacing `Data` manually does not discover new categories, and pure openpyxl cannot create native
PivotTable slicers or timelines. Those controls require a prebuilt Excel template, Excel COM, or an
Office Script; the generated dropdown controls remain macro-free and portable.

## Scheduled refresh on Windows

The operational task refreshes at logon and every hour, catches missed runs after sleep or
shutdown, and keeps all artifacts beside the non-synced ledger:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\tt-dashboard-task.ps1 -Mode Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\tt-dashboard-task.ps1 -Mode Status
```

The launcher generates a temporary `.xlsx` and replaces `dashboard.xlsx` only after the command
returns success and a JSON report. It atomically writes `health\dashboard-refresh.json` with the
timestamp, exit code, output path, and row-quality counters. `tt-doctor --strict-warnings` treats
missing or stale evidence, a failed refresh, a missing workbook, skipped JSONL rows, and duplicate
event ids as operational failures. The default freshness window is two hours and can be changed
with `TRACKER_DASHBOARD_STALE_SECONDS`.
