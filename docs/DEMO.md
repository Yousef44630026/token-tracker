# Demo script — presenting the AI Token Tracker to your tutor

~15-20 minutes. Every command below is verified to run. Run them from the tracker folder.
Set the Python once so commands are short:

```
cd "C:\Users\yerabhaoui\OneDrive - Deloitte (O365D)\Bureau\tracker"
set PY=C:\Users\yerabhaoui\python-portable\python.exe
```

Open two terminals: one for commands, one that will run the live dashboard.

---

## Act 1 — The problem (2 min, talk, no screen)

"Tracking LLM token usage sounds trivial but the market leaders get it wrong: Langfuse
double-counted Anthropic cache tokens (issue #12306), LiteLLM double-counted Gemini cache in
cost. The hard part is that each provider reports cache and reasoning tokens differently — some
inside the input count, some as separate buckets. Count them naively and you double-count.
I built a tracker whose entire design is about counting **exactly**, and proving it."

---

## Act 2 — "It counts exactly, and here's the machinery" (4 min)

Run the stage-by-stage trace of one Azure call:

```
%PY% examples\demo_trace_azure.py
```

Narrate the stages as they print:
- **Stage 2**: the adapter splits the usage into typed quantities and assigns *additivity* —
  cache is marked "subtotal of input", reasoning "subtotal of output".
- **Stage 4**: the derived rule — a subtotal counts as **0**, so `event_contributing_tokens`
  = input + output = 1490, and `event_total_mismatch = 0` means it reconciles to Azure's own
  total. "Zero mismatch is my proof of exactness."
- **Stage 5**: only source facts hit disk; totals are recomputed on read, so storage can never
  disagree with the rule.
- **Stage 6**: it lands attributed to a service.

One line to remember: **cache and reasoning are counted once, folded into input/output — never
added on top.**

---

## Act 3 — "It works on REAL provider data, and the tests prove it" (4 min)

These tests drive REAL captured Azure payloads through the real code (fast, seconds each):

```
%PY% tests\test_azure_real_matrix.py
%PY% tests\test_azure_real_stream_consume.py
%PY% tests\test_azure_responses_content_filter.py
```

Point at specific PASS lines:
- "GROUND TRUTH: cache is not double-counted — contributing == provider total" (the exact bug
  Langfuse had, tested on real Azure data).
- "GROUND TRUTH: sum(counted) == Azure total on a real streamed reasoning call" — a real 9-chunk
  Azure stream, reconciled.

Optional one-liner (the whole suite, if asked): `%PY% tests\run_all.py` → 190 scripts, 0 failures.

---

## Act 4 — "Watch it live" (4 min) — the memorable part

Terminal 2, start the live dashboard on a demo ledger:

```
set TL=%TEMP%\demo_ledger.jsonl
del %TL% 2>nul
%PY% -m tracker.export.live_dashboard --store %TL% --port 8790
```
Open http://127.0.0.1:8790 in a browser (put it next to the terminal).

Terminal 1, add usage for two different services and watch the dashboard animate:
```
%PY% examples\track_azure_service.py --service invoice-rag     --store %TL%
%PY% examples\track_azure_service.py --service contract-review --store %TL%
%PY% examples\track_azure_service.py --service invoice-rag     --store %TL%
```
Each command flashes a green row and a "+N tokens" toast, and the per-service table updates —
live, per service. "This is what an app team sees: their spend appears in real time, by service."

---

## Act 5 — "Why you can trust it, and it's deployable" (3 min)

- **Rigor**: I ran four adversarial audits and found two real bugs — an import that could double
  the whole history on a folder rename, and silent loss on a provider field rename — both fixed
  and re-proven. Show the register: `docs\OPERATIONAL_EVIDENCE.md` (proven vs assumed, with
  artifacts). "I separate what's proven from what's assumed and never overclaim."
- **Integration**: one line — `track_response(response, adapter, repository=repo, ...)`. No proxy
  required. Runbook: `docs\AZURE_TOMORROW.md`.
- **Status**: tagged `v0.3.0`, CI green on Windows and Ubuntu. Azure is deliverable today;
  Bedrock/Vertex are honestly gated until a real capture proves them.

---

## If a real Azure key is available (strongest possible ending)

```
set AZURE_OPENAI_API_KEY=...
set AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
set AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
scripts\tt-azure-smoke.cmd --json
```
Real Azure calls, tracked, reconciled — the live proof. If no key, Act 4 (demo mode) is already
convincing.

## Fallbacks / if something misbehaves

- Dashboard shows nothing: check the browser points at the same `--port`, and that a
  `track_azure_service` run printed `persisted: True`.
- A command errors about `pandas`: only the Excel export needs it; the live dashboard and tests
  do not — stay on the live dashboard.
- Keep the whole demo on the **demo ledger** (`%TEMP%`), so nothing touches the real
  billion-token ledger.

## The 3 sentences to land

1. "It counts each provider's cache and reasoning tokens exactly once — the thing the big tools
   get wrong — and proves it with `mismatch = 0` on real data."
2. "Everything derived is recomputed from stored facts, so the numbers can never silently drift."
3. "I proved it by trying to break it: four audits, two real bugs found and fixed, and an
   evidence register that says exactly what's proven versus assumed."
