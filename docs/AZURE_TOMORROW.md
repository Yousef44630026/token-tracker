# Azure test runbook — everything verified for real testing

Status (verified 2026-07-20, no code left to change on the Azure path):
- All 20 Azure fixtures reconcile exactly (`sum(counted) == provider_total`, mismatch 0).
- Real SDK **objects** (not just dicts) are read correctly, including nested
  `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens`.
- Streaming handles all three endings: clean (exact), cut (estimate, never mistaken for exact),
  timeout (unknown, never 0).
- Deployment name is recorded separately from the underlying model (multi-deployment safe).

## Step 1 — Set your Azure config (once, in the terminal you'll test from)

```
set AZURE_OPENAI_API_KEY=your-foundry-key
set AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
set AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
set AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT=text-embedding-3-large
```
(Optional: `AZURE_OPENAI_API_VERSION`, and `AZURE_OPENAI_RESPONSES_ENDPOINT` /
`AZURE_OPENAI_RESPONSES_DEPLOYMENT` if you use the Responses API.)

## Step 2 — Validate config + counting with the live smoke (real calls, all surfaces)

```
scripts\tt-azure-smoke.cmd --json
```
This makes real Azure calls on every configured surface (chat, embeddings, responses),
normalizes them, and reports contributing tokens per case. A surface with no config is
**skipped**, not failed. Use it to confirm your key works AND that counting reconciles before
touching your service. (It writes its own evidence file; it does not pollute the live ledger.)

## Step 3 — Wire your service (one line; the counting is already proven)

**Chat completions**
```python
from tracker import track_response
from tracker.adapters import create_adapter
from tracker.context.propagation import new_trace
from tracker.storage.file_repository import FileRepository

repo = FileRepository(r"C:\ai-token-tracker-data\collector_events.jsonl")
adapter = create_adapter("azure_openai", "chat_completions", deployment="gpt-5-mini")

response = client.chat.completions.create(model="gpt-5-mini", messages=[...])   # unchanged
track_response(response, adapter, repository=repo,
               context=new_trace(workflow="invoice-rag", environment="prod", business_id="billing-team"),
               observation={"authoritative": True, "status": "complete", "service_name": "invoice-rag"})
```

**Embeddings** — same, with `create_adapter("azure_openai", "embeddings", deployment="text-embedding-3-large")`.

**Streaming** — use `track_stream`, NOT `track_response`, and add `include_usage`:
```python
from tracker import track_stream
tr = track_stream(context=new_trace(workflow="chatbot"), provider="azure_openai",
                  api_surface="chat_completions", model="gpt-5-mini")
for chunk in client.chat.completions.create(..., stream=True, stream_options={"include_usage": True}):
    if chunk.choices and chunk.choices[0].delta.content:
        tr.feed(chunk.choices[0].delta.content)
    if getattr(chunk, "usage", None):
        event = tr.complete(output_tokens=chunk.usage.completion_tokens,
                            input_tokens=chunk.usage.prompt_tokens,
                            provider_total_tokens=chunk.usage.total_tokens)
        repo.append(event)
# if the stream is cut: tr.interrupt()   |   if it times out: tr.timeout(input_tokens=...)
```

## Step 4 — Prove counting is exact on YOUR real calls

```
C:\Users\yerabhaoui\python-portable\python.exe -c "from tracker.storage.file_repository import FileRepository as R; [print(e.observation.get('service_name'), e.event_contributing_tokens, 'mismatch=', e.event_total_mismatch) for e in R(r'C:\ai-token-tracker-data\collector_events.jsonl').iter_events() if e.provider=='azure_openai']"
```
Every line must end `mismatch= 0`. If so, your Azure counting is exact.

## Step 5 — See it per service

```
scripts\tt-dashboard.cmd
```
Open `C:\ai-token-tracker-data\dashboard.xlsx`. (The Dashboard Refresh task already regenerates it hourly.)

## The three traps (all handled, but do these)

1. **Streaming** → `track_stream` + `stream_options={"include_usage": True}`. On plain
   `track_response` a streamed call reports `raw_usage_missing` (0 tokens). This is the #1 trap.
2. **`service_name` on every call** → otherwise everything lands under `unknown`.
3. **One `create_adapter(..., deployment=...)` per Azure deployment** → keeps per-deployment
   attribution correct.

## What "wrong" looks like (the tracker never stays silent)

- `raw_usage_missing` → no usage in the response (usually streaming without `include_usage`).
- `provider_schema_drift` → Azure returned an unknown usage field (API changed); the field names
  are recorded in `observation["unmapped_usage_fields"]` as an early warning.
- `provider_total_mismatch` → counted sum ≠ Azure's total. Should never happen on Azure; if it
  does, capture the payload and report it.
