# Connecting an application (Azure OpenAI, AWS Bedrock, Google Vertex/Gemini)

This is the operator guide for pointing a real application at the tracker so its token usage
is captured per service. Read the readiness table first — not every cloud is certified for
production capture yet.

## Cloud readiness (as of this doc; source of truth is `tt-provider-matrix`)

| Cloud / surface | Capture status | Safe to connect in production? |
|---|---|---|
| **Azure OpenAI** — chat, responses, embeddings | Verified against 18 real captures; cache and streaming **proven** | **Yes.** |
| **AWS Bedrock** — Converse / InvokeModel / embeddings | Cache accounting follows AWS docs; **no real cached payload captured yet** | **Not until** `tt-bedrock-cache-smoke` runs with real AWS credentials. Falsifiable but unproven. |
| **Google Gemini** (Developer API) | 1 real capture; cache not yet real-validated | Usage yes; treat cache figures as unverified. |
| **Google Vertex AI** | Simulated fixtures only; wire/auth/region not captured | **No.** A Gemini capture does not certify Vertex. Label experimental. |

## Two ways to connect

### A. Reverse proxy (no application code change)
Point the app's provider `base_url` at the local proxy; it relays to the real upstream and
records usage. One proxy instance per provider.

```console
ai-token-tracker-proxy serve --provider azure_openai --upstream https://YOUR-RESOURCE.openai.azure.com --port 8080 --store C:\ai-token-tracker-data\collector_events.jsonl
```
Then set the SDK's `base_url` / endpoint to `http://127.0.0.1:8080`. Non-loopback binds require
an auth token (see `tt-local-auth`).

### B. In-code (`track_response`)
Wrap the SDK call; nothing is proxied.

```python
from tracker import track_response
from tracker.adapters import create_adapter
from tracker.context.propagation import new_trace
from tracker.models.trace import Trace

ctx = new_trace(workflow="invoice-rag", environment="prod", business_id="billing-team")
trace = Trace(trace_id=ctx.trace_id)
result = track_response(sdk_response, create_adapter("azure_openai", "chat_completions"), context=ctx, trace=trace)
```

## Per-service attribution — REQUIRED for useful dashboards

Token counts are correct without attribution, but "which service/team/environment spent them"
needs the caller to supply it. Two mechanisms:

- **Cross-service HTTP headers** (`X-TokenTracker-*`), propagated automatically:
  `Trace-Id`, `Span-Id`, `Request-Correlation-Id` (required), plus `Business-Id`, `Workflow`,
  `Environment` (attribution dimensions surfaced in the dashboard).
- **Observation metadata** for finer keys the dashboard groups by: `service_name`, `tenant`,
  `cloud_provider`, `region` — set via `observation={...}` on the tracked call or the proxy
  config. Missing → `unknown` (never guessed).

Without at least `service_name`/`Business-Id`, every event lands under `unknown` and the
per-service view is empty. Make it mandatory in your onboarding checklist.

## Per-cloud notes

- **Azure OpenAI**: the response body carries the underlying `model`; pass the Azure deployment
  name to the adapter so it is recorded separately (`deployment` column) without overwriting
  `model`. Cache (`cached_tokens`) is a subtotal of input; reasoning is a subtotal of output —
  both already proven, no double counting.
- **AWS Bedrock**: `inputTokens` is documented as non-cached input; `cacheReadInputTokens` /
  `cacheWriteInputTokens` are separate additive buckets. This is **doc-based, not payload-proven**
  — run the two-call smoke (`tt-bedrock-cache-smoke`) before trusting cache figures. Cohere Embed
  on Bedrock returns no token count (declared unsupported, not silently zero).
- **Gemini / Vertex**: `thinking` is added on top of output (contributing); `cachedContent` is a
  subtotal of input. Vertex shares Gemini's accounting rules but its wire identity, auth, and
  regional routing are uncaptured — capture a real Vertex payload before production.

## Before you connect a new cloud in production

1. `tt-provider-matrix` shows the surface `pass` (or you accept an explicit `warn`).
2. For Bedrock: `tt-bedrock-cache-smoke` has run and reconciled.
3. The app sends `service_name`/`Business-Id` on every call.
4. `tt-doctor --strict-warnings` is green and the collector requires auth.
