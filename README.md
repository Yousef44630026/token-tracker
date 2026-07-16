# AI Token Tracker (Architecture v9)

Token tracking layer for GenAI, RAG, and agentic systems, built on the standard library
(+ `openpyxl` for Excel). Capture and accounting contain **no pricing and no SQL/DB**.
The optional reporting extra may derive presentation-only cost from an external price table.

> Status: the accounting core, operational collector, exports, and non-live regression suite
> are implemented. Provider readiness remains evidence-based: consult
> `docs/OPERATIONAL_EVIDENCE.md` and the provider matrix before claiming a surface validated.

## Core idea — storage vs. derived

The single most important boundary in this codebase:

- **Stored = source of truth only.** Schema v9 stores observed facts, including the independent
  `overlap` and `trust` axes, provider totals, hashes, and typed observation metadata.
- **Derived = computed, never stored, never serialized.** Anything that can be recomputed
  (`included_in_total`, `quantity_in_total`, `export_warning`, `event_contributing_tokens`,
  supersession, normalizer flags, trace totals) is **excluded** from the JSONL.

Totals sum `quantity_in_total` **only** — never the raw `quantity` column, never
`provider_total_tokens` across events. This is what prevents double-counting of cached /
reasoning / subtotal quantities and of superseded (retried/streamed) events.

## Invariants (enforced in code AND tests)

- **INV-1** Storage holds source-of-truth fields only.
- **INV-2** Derived fields are computed-only and absent from serialized JSONL.
- **INV-3** `token_type` says *what* the tokens are, never *how well measured* (precision is separate).
- **INV-4** Overlap (`independent` / `subtotal_of`) and trust (`verified` / `unverified`)
  are separate axes assigned by provider rules; the legacy flat additivity enum is a runtime view.
- **INV-5** Supersession is correlated by `request_correlation_id`; a superseded event contributes 0.
- **INV-6** Unknown is not zero — a lost quantity is `None`/`unknown`, surfaced as a count.
- **INV-7** Operational authority is an explicit typed boolean. Missing authority fails closed,
  is flagged, and live v9 ingestion rejects the payload.

## Layout

```
tracker/
  context/        propagation + cross-service headers
  models/         trace, span, token_event, token_quantity, enums   (source of truth)
  adapters/       per-provider API-surface adapters
  normalization/  additivity, reconciler, supersession, data_quality, normalizer
  derive/         derived fields + trace rollup   (computed only)
  classification/ precision + unknown-reason classifiers
  streaming/      stream tracker
  estimation/     local tokenizer, historical forecaster
  workflows/      rag + agent span helpers
  analytics/      coverage, exactness, anomaly signals
  export/         csv + excel exporters
  collector/      non-blocking collector client
  storage/        event JSONL + atomic complete-trace snapshots
  service.py      public response/stream tracking façade
api/              threaded standard-library HTTP collector
tracker/proxy/    loopback real-call relay + TokenTap-style estimate comparison
tests/            phase tests (plain runnable scripts — no pytest in this env)
```

## Basic use

```python
from tracker import track_response
from tracker.adapters import create_adapter
from tracker.context.propagation import new_trace
from tracker.models.trace import Trace

context = new_trace(trace_id="run-123", workflow="rag")
trace = Trace(trace_id=context.trace_id)

result = track_response(
    provider_response,
    create_adapter("openai", "responses"),
    context=context,
    trace=trace,
)
event = result.event
```

For streaming, `track_stream(context=..., provider=..., api_surface=...)` creates a
`StreamTracker` without manually copying trace identity fields.

## Operational pre-flight

Before running real provider traffic, run the doctor. It checks the local Python/runtime,
Excel dependency, storage/derived invariant, collector network posture, local secret leaks,
Azure/Foundry env profiles, collector-health freshness, scheduled Claude-import freshness,
store writability, and whether an existing JSONL/partitioned store can be read by streaming
over events.

```console
scripts\tt-doctor.cmd --store real_call_events.jsonl
```

Installed entry point:

```console
ai-token-tracker-doctor --store real_call_events.jsonl
```

For partitioned high-volume stores:

```console
ai-token-tracker-doctor --store runs\events --partitioned-store
ai-token-tracker-proxy report --store runs\events --partitioned-store
ai-token-tracker-proxy powerbi-export --store runs\events --partitioned-store --output powerbi_dataset
```

The collector supports the same mode with `--partitioned-store` or
`TRACKER_PARTITIONED=true`; in that mode `TRACKER_STORE` names the partition root.

The Power BI exporter consumes the repository iterator once into a temporary, event-id
deduplicated SQLite snapshot. Fact CSVs are then written from replayable iterators, avoiding
an in-memory copy of the full event history; the temporary snapshot is deleted afterward.

Exit code is non-zero only for blockers (`FAIL`), unless `--strict-warnings` is used.
`WARN` means the tracker can run but something deserves attention, e.g. no store exists yet,
a local ignored `.env` contains credentials, or Azure/Foundry env vars are only partially
configured. `FAIL secret-scan` means a credential-shaped value is in a project file that could
be shared or committed; rotate that credential before continuing.

### Azure live smoke

Once Azure credentials are present in the current terminal, run the tiny live smoke harness.
It makes one short call per configured surface, writes raw payloads/errors, `events.jsonl`,
CSV, Excel, `trust_report.json`, and a small `README_AUDIT.md` under
`runs\azure-smoke\<timestamp>`.

```console
scripts\tt-azure-smoke.cmd --require-live
```

The harness runs zero-cost skips for missing optional surfaces. Configure any subset. For
Azure AI Foundry / OpenAI v1 Responses only:

```powershell
$env:AZURE_OPENAI_API_KEY = "<key>"
$env:AZURE_OPENAI_RESPONSES_ENDPOINT = "https://your-resource.services.ai.azure.com/openai/v1"
$env:AZURE_OPENAI_RESPONSES_DEPLOYMENT = "your-responses-deployment"
```

For classic Azure OpenAI chat/embeddings deployment routes:

```console
set AZURE_OPENAI_API_KEY=...
set AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
set AZURE_OPENAI_DEPLOYMENT=your-chat-deployment
set AZURE_OPENAI_API_VERSION=2024-10-21
set AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT=your-embeddings-deployment
```

`config_redacted.json` records only whether the API key is present and lists the configured
profiles (`foundry-responses`, `azure-chat`, `azure-embeddings`); it never writes the key.

Dry-run without provider calls:

```console
scripts\tt-azure-smoke.cmd --dry-run --json
```

### Bedrock cache smoke

The Bedrock harness makes two identical Converse calls with a unique explicit cache checkpoint.
It passes only when the first call reports a cache write, the second reports a cache read, and
both responses reconcile. Audit artifacts contain usage and technical metadata only; prompts,
generated content, and credentials are never written.

```powershell
$env:AWS_REGION = "us-east-1"
$env:BEDROCK_MODEL_ID = "a-cache-capable-model-id"
scripts\tt-bedrock-cache-smoke.cmd --dry-run --json
scripts\tt-bedrock-cache-smoke.cmd --require-live
```

Live execution requires `pip install -e ".[bedrock]"` and AWS credentials available through the normal SDK credential
chain or `AWS_BEARER_TOKEN_BEDROCK`. Cache minimums vary by model; the default static prefix is
5,000 words and `--prefix-words` can be adjusted from the model documentation. This is a billed
two-call test, although output is capped at eight tokens per call. See the official
[Bedrock prompt-caching guide](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
for supported models, Regions, checkpoint locations, and minimum cache sizes.

### OpenTelemetry metric sink

`tracker.export.otel_projection` exposes a dependency-free
`gen_ai.client.token.usage` projection. An application with an existing Meter can attach it with
`recorder_from_meter(meter)`. The optional `otel` extra also provides an isolated OTLP/HTTP
runtime; neither path replaces JSONL or enters capture/storage code. See
[`docs/OPENTELEMETRY.md`](docs/OPENTELEMETRY.md).

### Local check suite

Run the local gate with:

```console
scripts\tt-check.cmd
```

It runs Ruff plus the core storage/accounting, Azure adapter, smoke harness, proxy, API,
Power BI, collector rejection, invalid-row tolerance, and deep fuzz regressions.

### Thread pools

ContextVars flow into asyncio tasks automatically but NOT into a raw
`ThreadPoolExecutor.submit()` — an LLM call made inside a plain pool silently becomes its
own root, with no `propagation_lost` flag. Use the propagation-aware tools instead:

```python
from tracker.context.threads import ContextPropagatingExecutor, carry_context

with ContextPropagatingExecutor(max_workers=8) as pool:   # drop-in ThreadPoolExecutor
    future = pool.submit(call_llm, prompt)                 # worker sees YOUR span

wrapped = carry_context(call_llm)                          # or wrap one callable
```

### Providers without a dedicated adapter

`create_adapter(provider, surface)` stays strict (unknown pairs raise). When a call must
never be dropped, resolve with fallback instead:

```python
from tracker.adapters.registry import create_adapter_with_fallback

adapter = create_adapter_with_fallback("groq", "chat_completions")
```

An unknown provider is captured OPEN and counted CLOSED: the payload's real counts are kept
(common key spellings only — nothing is ever invented), every quantity is `unverified`
(fail-closed additivity, INV-4), so the event is present in the audit trail, raises
`unverified_additivity`, and contributes 0 to totals until a dedicated adapter encodes the
provider's verified additivity truth.

## Collector service

When installed, run the threaded collector with:

```console
ai-token-tracker-collector --store collector_events.jsonl --host 127.0.0.1 --port 8787
```

Durable `fsync` writes are enabled by default; `--no-durable` is an explicit operational
opt-out. HTTP ingestion is idempotent by
`event_id`, bounded by request and batch limits, and uses the same source-of-truth
validation as local ingestion.

Collector transports are at-least-once after an in-flight timeout expires, so custom
transports should preserve the same `event_id` idempotency contract as the bundled HTTP
collector.

Set `TRACKER_AUTH_TOKEN` (or pass `--auth-token`) to require bearer authentication for
ingestion and stats. Keep the collector on loopback or behind TLS when used beyond the
local machine.

On Windows, inspect and install the per-user supervised task with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tt-collector-task.ps1 -Mode Plan
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tt-collector-task.ps1 -Mode Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tt-collector-task.ps1 -Mode Status
```

The task starts at Windows startup and logon, supervises and restarts a failed collector child, keeps logs beside
the non-synced event store, and never serializes `TRACKER_AUTH_TOKEN`. Use `-Mode Stop` for maintenance and
`-Mode Uninstall` to remove the task.

Install the independent one-minute health monitor with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tt-collector-monitor-task.ps1 -Mode Plan
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tt-collector-monitor-task.ps1 -Mode Install
```

Run a strict 72-hour availability and append-only integrity soak with:

```powershell
scripts\tt-collector-soak.cmd --duration-seconds 259200 --interval-seconds 60
```

The soak records samples, outages, latency aggregates, monotonic counter checks, and a SHA-256
proof that the store prefix present at startup was not modified in place.

## Excel dashboard reporting

The optional pandas/openpyxl reporting layer generates an interactive native Excel dashboard
plus four audit sheets without adding pricing or KPI fields to stored events:

```powershell
python -m pip install -e ".[reporting]"
scripts\tt-dashboard.cmd --data-dir .\data --prices .\prices.csv --output .\dashboard.xlsx
```

On Windows, install the sleep/shutdown-tolerant hourly refresh task after the operational store
has been configured. It publishes a completed workbook atomically and writes freshness evidence
beside the store; a failed run keeps the previous known-good workbook:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\tt-dashboard-task.ps1 -Mode Plan
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\tt-dashboard-task.ps1 -Mode Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\tt-dashboard-task.ps1 -Mode Status
scripts\tt-doctor.cmd --store C:\ai-token-tracker-data\collector_events.jsonl --strict-warnings
```

The task runs at logon and every hour with `StartWhenAvailable`. The doctor fails when the latest
refresh is unhealthy, older than two hours, missing its workbook, or reports skipped/duplicate
source rows. Missing prices remain `total_cost=null`; scheduling never invents a price.

See `docs/EXCEL_DASHBOARD.md` for the exact stored schema, the external price-table contract,
supersession rules, cost allocation, interactive filters, KPI definitions, and refresh limitations.

`FileRepository` serializes concurrent same-process writers targeting the same path, supports
idempotent `append_unique()`, and can recover a crash-truncated final JSONL line. Use
`recover_truncated_tail=False` when strict corruption detection is preferred.

CSV bundles include a `manifest.json` with row counts, SHA-256 hashes, schema version, the
canonical observed total, and the only columns that are safe to sum at each grain.

## Real-call proxy comparison

The optional loopback proxy compares a TokenTap-style pre-flight prompt estimate with the
exact usage returned by Anthropic or OpenAI. It stores request/response hashes and token
facts only: authorization headers and raw prompts are never persisted.

Any OTHER provider (groq, together, an OpenAI-compatible gateway...) can be proxied too by
naming its upstream explicitly — traffic on the common path shapes (`/chat/completions`,
`/responses`, `/messages`, non-streaming or SSE) is captured through the generic fallback
adapter instead of passing through unmeasured:

```console
ai-token-tracker-proxy serve --provider groq --upstream https://api.groq.com/openai --store groq_events.jsonl
```

Install the optional tokenizer to reproduce TokenTap's `cl100k_base` measurement:

```console
pip install -e ".[proxy]"
```

Keep the live append-only event store on a non-synced local volume. OneDrive, Dropbox, and
Google Drive can hold exported copies, but should not own the JSONL ledger or its lock/index
sidecars. `tt-doctor` reports the active tokenizer backend and warns when the configured
store resolves inside a recognized sync folder.

Run Claude Code through the proxy and save events to JSONL:

```console
ai-token-tracker-proxy run --provider anthropic --store real_call_events.jsonl -- claude
```

If the console-script directory is not on `PATH`, use the installed Python module:

```console
python -m tracker.proxy.cli run --provider anthropic --store real_call_events.jsonl -- claude
```

Or keep the proxy running for an SDK/client configured separately:

```console
ai-token-tracker-proxy serve --provider openai --store real_call_events.jsonl --port 8080
```

Then set `OPENAI_BASE_URL=http://127.0.0.1:8080` for OpenAI-compatible API clients.
Codex with ChatGPT authentication should not be forced through the proxy: the ChatGPT
login path can fail on missing API scopes. For Codex, use the local `token_count` import
flow documented in `docs/CODEX_TRACKING.md`; it tracks Codex usage after each run without
storing raw prompts or credentials.

Claude Code can also be imported from its local usage-bearing transcripts without storing
prompt or assistant content:

```console
scripts\tt-claude-import.cmd
scripts\tt-claude-import-task.ps1 -Mode Plan
scripts\tt-claude-import-task.ps1 -Mode Install
scripts\tt-claude-import-task.ps1 -Mode Status
```

The scheduled importer maintains an atomic byte checkpoint at
`<store-parent>\health\claude-import-state.json`, advances it only after complete collector
acknowledgement, and defers an incomplete transcript tail until its newline arrives. It exits
`2` on suspected transcript-format drift and does not advance the checkpoint or post events.
When collector authentication is enabled, set `TRACKER_AUTH_TOKEN` in the task user's
environment; the token is sent as a bearer header at runtime and is never written into the
task definition, checkpoint, or log. The collector response distinguishes valid `acked` ids
from newly `persisted` ids, so duplicate replay is explicit rather than reported as new data.

The exact provider input remains the contributing quantity. The estimate is attached under
the input quantity's `metadata.prompt_estimate`, including the estimator name and the
provider-minus-estimate difference. For Anthropic, the comparison denominator is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`; OpenAI cached tokens
remain a subtotal of input. Successful startup/probe responses without usage are ignored,
while provider/network errors remain observable. Every recorded event receives a UTC
timestamp.

Each new proxy event also stores an `observation` block containing operational
source-of-truth facts:

- `status` and `authoritative`: complete calls contribute to totals; failed/incomplete calls
  remain visible but contribute zero;
- provider HTTP status, request id, and response id when supplied;
- proxy session id, request sequence, prompt fingerprint/sequence/cycle;
- response-header latency, time to first streamed output token, and total duration.

Prompt attribution uses a SHA-256 fingerprint of the latest human text. The prompt itself is
not persisted. Anthropic cache creation retains its 5-minute and 1-hour token breakdown when
the provider reports it.

After a run, generate an aggregate reliability report:

```console
ai-token-tracker-proxy report --store real_call_events.jsonl
```

For repeatable prompt testing, run a Markdown suite. The proxy starts one isolated local
proxy per prompt, so all API calls made for that prompt are grouped under the same
`suite_prompt_*` metadata. The raw prompt is passed to the child process but is not
persisted; events store only the label, sequence, source file, and SHA-256 fingerprint.

```console
ai-token-tracker-proxy prompt-suite \
  --provider anthropic \
  --store suite_events.jsonl \
  --prompts RELIABILITY_TEST.md \
  -- claude -p "{prompt}" --safe-mode --no-session-persistence --output-format json
```

Use `--dry-run` to verify labels/hashes without making provider calls. The final report
includes a `per-prompt` section with event count, incomplete count, exact provider tokens,
output tokens, and estimate/provider ratio for each prompt.

Add `--quality-checks` to evaluate known scenario outputs while the child process is still
running. The raw answer is not written to JSONL; only pass/fail is printed to the console.
Use `--fail-on-quality` when you want the command to return non-zero on quality failures.
Add `--suppress-output` when you want the suite runner to capture child stdout/stderr for
checks without echoing raw prompt answers back to the terminal.

Add `--live-budget-tokens` to see a live usage bar. This is a tracker-observed provider
token budget, not a Claude Pro/ChatGPT Plus account-balance lookup. Existing complete
events in the target store count as already used, so the bar works naturally with
`--resume-complete`.

```console
ai-token-tracker-proxy prompt-suite \
  --provider anthropic \
  --store suite_events.jsonl \
  --prompts RELIABILITY_TEST.md \
  --live-budget-tokens 300000 \
  -- claude -p "{prompt}"
```

To count raw prompt text before sending it to a model, use `count-prompt`. This is a local
TokenTap-style `cl100k_base` estimate when `tiktoken` is installed; it does not make a
provider call and should not be confused with full provider usage, which may include hidden
system/tool/context tokens.

```console
ai-token-tracker-proxy count-prompt "your prompt here" --budget-tokens 50000
ai-token-tracker-proxy count-prompt --interactive --budget-tokens 50000
```

For interactive Codex, use the local Codex watcher rather than the proxy. It polls local
Codex `token_count` logs while Codex is running and prints the bar after each detected
model call:

```console
scripts\tt-codex-interactive.cmd
```

If a long suite is interrupted, rerun with `--resume-complete` to skip prompts that already
have complete authoritative events in the target store. Use `--start N` for a manual
1-based resume point.

```console
ai-token-tracker-proxy prompt-suite \
  --provider anthropic \
  --store suite_events.jsonl \
  --prompts RELIABILITY_TEST.md \
  --resume-complete \
  -- claude -p "{prompt}"
```

Audit a capture for obvious prompt or credential leakage:

```console
ai-token-tracker-proxy privacy-audit --store suite_events.jsonl --prompts RELIABILITY_TEST.md
```

Export the per-prompt token table to CSV:

```console
ai-token-tracker-proxy report \
  --store suite_events.jsonl \
  --per-prompt-csv suite_prompt_results.csv
```

See `RELIABILITY_TEST.md` for a repeatable prompt suite covering short prompts, multilingual
text, file/tool calls, cache creation, cache reuse, and multi-file reasoning.
For Codex/Claude smoke suites used during live testing, see `CODEX_VARIED_TESTS.md` and
`CLAUDE_VARIED_TESTS.md`.

## Running tests (this environment)

Tests run as plain scripts with the configured Python:

```
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\run_all.py
```

Each test prints `[PASS]/[FAIL]` lines and exits non-zero on failure. The six core
falsifying tests (added in their phases) are the permanent regression set:
`additivity_no_double_count`, `event_grain_no_double_count`,
`storage_no_stored_derived_fields`, `stream_supersession_no_double_count`,
`export_totals_match_model`, and the context-propagation harness.

## Build phases

0. Scaffold (this commit) · 1. Context propagation · 2. Core models + enums ·
3. Additivity + derived + supersession · 4. Adapter contract · 5. OpenAI adapters ·
6. Precision/unknown classifiers · 7. Streaming · 8. Safe-failure collector ·
9. CSV + Excel export · 10. Bedrock + Gemini adapters · 11. RAG + agent helpers.
