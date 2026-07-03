# AI Token Tracker (Architecture v9)

Token tracking layer for GenAI, RAG, and agentic systems — built on the standard library
(+ `openpyxl` for Excel). **No pricing logic. No SQL/DB.** Storage supports append-only
event JSONL and atomic complete-trace snapshots; CSV and Excel exports include events,
quantities, and spans.

> Status: the core library and 60+ non-live regression scripts are implemented. Provider
> adapter tests currently use **SIMULATED fixtures** (`tests/fixtures/*.SIMULATED.json`)
> built to documented usage shapes. Replace them with recorded payloads for ground-truth
> verification. Unregistered provider/token combinations fail closed as
> `additivity="unverified"` and do not affect totals.

## Core idea — storage vs. derived

The single most important boundary in this codebase:

- **Stored = source of truth only.** `TokenEvent` / `TokenQuantity` store raw, observed
  facts (token_type, quantity, precision, additivity, provider totals, hashes…).
- **Derived = computed, never stored, never serialized.** Anything that can be recomputed
  (`included_in_total`, `quantity_in_total`, `export_warning`, `event_contributing_tokens`,
  trace totals…) is a `@property` / pure function and is **excluded** from the JSONL.

Totals sum `quantity_in_total` **only** — never the raw `quantity` column, never
`provider_total_tokens` across events. This is what prevents double-counting of cached /
reasoning / subtotal quantities and of superseded (retried/streamed) events.

## Invariants (enforced in code AND tests)

- **INV-1** Storage holds source-of-truth fields only.
- **INV-2** Derived fields are computed-only and absent from serialized JSONL.
- **INV-3** `token_type` says *what* the tokens are, never *how well measured* (precision is separate).
- **INV-4** Additivity (`total_contributing` / `subtotal_of` / `unverified`) is set by the adapter, never inferred from the type string.
- **INV-5** Supersession is correlated by `request_correlation_id`; a superseded event contributes 0.
- **INV-6** Unknown is not zero — a lost quantity is `None`/`unknown`, surfaced as a count.
- **INV-7** Trace/event/span identities must agree; invalid and duplicate aggregate members
  are rejected at the model boundary.

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

## Collector service

When installed, run the threaded collector with:

```console
ai-token-tracker-collector --store collector_events.jsonl --host 127.0.0.1 --port 8787
```

Add `--durable` to `fsync` acknowledged batches. HTTP ingestion is idempotent by
`event_id`, bounded by request and batch limits, and uses the same source-of-truth
validation as local ingestion.

Collector transports are at-least-once after an in-flight timeout expires, so custom
transports should preserve the same `event_id` idempotency contract as the bundled HTTP
collector.

Set `TRACKER_AUTH_TOKEN` (or pass `--auth-token`) to require bearer authentication for
ingestion and stats. Keep the collector on loopback or behind TLS when used beyond the
local machine.

`FileRepository` serializes concurrent same-process writers targeting the same path, supports
idempotent `append_unique()`, and can recover a crash-truncated final JSONL line. Use
`recover_truncated_tail=False` when strict corruption detection is preferred.

## Real-call proxy comparison

The optional loopback proxy compares a TokenTap-style pre-flight prompt estimate with the
exact usage returned by Anthropic or OpenAI. It stores request/response hashes and token
facts only: authorization headers and raw prompts are never persisted.

Install the optional tokenizer to reproduce TokenTap's `cl100k_base` measurement:

```console
pip install -e ".[proxy]"
```

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

`pytest`/`ruff`/`black` are not installed and installs are disabled, so tests run as
plain scripts with the portable Python:

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
