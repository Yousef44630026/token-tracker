# Excel Dashboard Reporting

The Excel dashboard is an optional presentation layer. JSONL remains the source of truth;
cost, safe totals, request counts, latency statistics, and chart tables are derived only in
memory and in the generated workbook.

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

The workbook is regenerated from scratch and contains exactly `Data`, `Coûts`,
`Tokens & Latence`, and `Use cases`. Charts are native Excel objects. Their helper tables are
rebuilt by the script; rerun the command after changing JSONL or prices. Replacing the `Data`
sheet manually does not discover new dates/models/use cases because openpyxl cannot create and
refresh native PivotCaches from scratch.
