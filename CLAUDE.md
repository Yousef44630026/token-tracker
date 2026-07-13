# Claude Code Deployment Prompt — AI Token Tracker (Architecture v8)

## Current repository decisions

The embedded bootstrap prompt below remains the architectural origin, with these binding
updates for the current repository:

- Ruff is the only lint/style gate. Black is intentionally not a dependency or CI gate.
- JSONL remains the sole event source of truth. A disposable SQLite event-id index is
  permitted only as a reconstructible acceleration structure and never stores accounting
  totals or replaces the JSONL ledger.
- Current CI must run the six named falsifying invariants plus the complete non-live suite.

## How to use
1. Create an empty (or to-be-replaced) folder for the project.
2. Optional but recommended: drop `ai_token_tracker_architecture_v8.md` into it as the source of truth.
3. Open Claude Code at the folder root.
4. Paste everything in the **PROMPT** block below (from `You are building...` to the final line). It is self-contained: it embeds every binding invariant and per-phase test, so it works with or without the v8 spec file present.

This prompt is deliberately **phased, test-first, and gated**. Do not rewrite it into a single "build everything" instruction — the gates are what keep the additivity and supersession logic trustworthy.

---

## PROMPT

```
You are building an AI token tracking layer for GenAI, RAG, and agentic systems, entirely from scratch.

If ai_token_tracker_architecture_v8.md exists in this directory, read it completely first — it is the full source of truth. The constraints, invariants, data model, and phases below are BINDING regardless of whether that file is present.

=====================================================================
PRE-FLIGHT — do this first, then STOP and wait for me to type "go"
=====================================================================
1. Print the absolute path of the current working directory and list its contents.
2. If this is not a git repository, run `git init`. Stage and commit everything currently present as "snapshot before token-tracker rebuild" so prior contents are recoverable.
3. State exactly what you will delete or replace to start from a clean slate.
4. WAIT for me to type "go" before deleting anything or writing code.

=====================================================================
HARD CONSTRAINTS (never violate)
=====================================================================
- Language: Python 3.11+. Use dataclasses, full type hints, pytest, and Ruff. Excel via openpyxl.
- FROM SCRATCH: no OpenTelemetry, Langfuse, Datadog, LangSmith, Helicone, or any observability SDK. Standard library + pytest/Ruff + provider SDKs (for capturing test fixtures only).
- Event storage is JSONL, exported to CSV/Excel. SQL/ORM accounting storage is forbidden;
  a disposable, reconstructible SQLite event-id index is allowed for partition lookup only.
- NO pricing logic anywhere.
- No localStorage/sessionStorage-style hacks, no fabricated provider fields, no invented token counts.

=====================================================================
NON-NEGOTIABLE INVARIANTS — enforce in code AND in dedicated tests
=====================================================================
INV-1 (Storage = source of truth only).
  TokenQuantity STORES: token_type, token_role, quantity (Optional[int]), precision_level,
    usage_source, additivity, subtotal_of, aggregation_mode, unknown_reason, metadata.
  TokenEvent STORES: event_id, request_correlation_id, identity/context/provider fields,
    quantities[], provider_total_tokens, superseded, superseded_by, data_quality_flags,
    hashes, timestamp, observation.

INV-2 (Derived, never stored, never serialized into JSONL).
  These are @property / computed only, and the JSONL serializer MUST exclude them:
    included_in_total, quantity_in_total, export_warning,
    event_contributing_tokens, event_total_mismatch, all trace totals.
  Derivation:
    included_in_total = (additivity == "total_contributing") and (quantity is not None)
    quantity_in_total = quantity if included_in_total else 0
    export_warning    = "subtotal_excluded_from_total"              if additivity == "subtotal_of"
                        "unverified_additivity_excluded_from_total"  if additivity == "unverified"
                        "unknown_quantity_excluded_from_total"       if quantity is None and precision_level == "unknown"
                        else None
    event_contributing_tokens = 0 if event.superseded or observation.authoritative == false
                                else sum(q.quantity_in_total for q in event.quantities)
    event_total_mismatch      = provider_total_tokens - sum(q.quantity_in_total)  # if provider_total_tokens is not None
    trace.observed_total_contributing_tokens = sum(event.event_contributing_tokens for event in trace.events)

INV-3 (token_type purity).
  token_type encodes WHAT the tokens are, never how well they were measured.
  FORBIDDEN token types: partial_output_observed, estimated_input, estimated_output.
  A partial stream is token_type="output", precision_level="estimate", usage_source="partial_stream_tokenizer".
  An estimate is the same type with precision_level="estimate".
  Allowed token types: input, output, cached_input, cache_creation_input, reasoning, thinking,
    embedding, rerank_input, rerank_output, audio_input, audio_output, image_input, video_input.
  `total` is NOT a token type; provider total is event-level raw data.

INV-4 (Additivity is adapter-assigned, never inferred from the type string).
  additivity ∈ {total_contributing, subtotal_of, unverified}.
  Per-provider truth (the adapter sets this):
    OpenAI Responses & Chat Completions: input, output = total_contributing;
      cached_input = subtotal_of input; reasoning = subtotal_of output.
    Gemini Generate Content: input, output = total_contributing;
      cached_input = subtotal_of input; thinking = total_contributing (added on top).
    Bedrock Converse cache fields = "unverified" (contribute 0, raise flag)
    Anthropic Messages cache_read/cache_creation = "total_contributing" because Anthropic
    reports them as distinct input buckets alongside input_tokens.
  Totals sum quantity_in_total ONLY. The raw quantity column is NEVER summed.
  provider_total_tokens is raw provider data and is NEVER summed across events.
  subtotal_of is a single parent (string) for all current providers.

INV-5 (Supersession, correlated).
  A superseded event contributes 0 everywhere (event_contributing_tokens == 0).
  A partial estimate is matched to its final-usage event by request_correlation_id
    (NOT span_id — a span may contain retries = multiple calls).
  On match: partial.superseded=True, partial.superseded_by=final.event_id, flag "superseded".
  Supersession is set by the reconciler / stream tracker, never by an adapter.

INV-6 (Unknown is not zero).
  A lost output is quantity=None, precision_level="unknown", contributes 0, and is surfaced as a COUNT,
  never summed into a measured total as zero-with-confidence.

INV-7 (Operational authority is explicit).
  observation.status records complete / failed / incomplete.
  observation.authoritative=false preserves the event for audit but forces its contributing
  total to 0 in model, trace rollup, CSV, Excel, and coverage calculations.
  Provider IDs, HTTP status, timing, proxy session/sequence, and prompt fingerprint are
  source-of-truth observation facts; raw prompt text and credentials are never stored.

DATA-QUALITY FLAGS — each has exactly ONE producer:
  provider_total_mismatch (normalizer: provider_total != sum(quantity_in_total))
  unverified_additivity   (normalizer: any quantity additivity == "unverified")
  unknown_quantity_present(normalizer: any quantity precision_level == "unknown")
  partial_stream_estimate (stream tracker)
  stream_interrupted      (stream tracker)
  superseded              (reconciler / stream tracker)
  propagation_lost        (context propagation layer: parent unresolved)
  raw_usage_missing       (usage extractor: no usage object)
  normalization_error     (normalizer: adapter raised)

=====================================================================
WORKING METHOD (strict)
=====================================================================
- Test-first, red-green. Each phase: write the failing test(s) FIRST, run them (must fail for the right reason),
  implement to green, then run Ruff.
- Build strictly in phase order. Do NOT start a phase until the previous phase's tests are green.
- At the END of each phase: run the full suite, print a short summary (built / passing / failing), and STOP.
  Wait for me to type "next" before the following phase. Do not run multiple phases without stopping.
- Adapter tests use RECORDED REAL provider payloads (capture from the SDK). Do NOT hand-write idealized fixtures —
  they would just encode assumptions back as truth.

=====================================================================
PACKAGE STRUCTURE (create exactly this in Phase 0)
=====================================================================
ai-token-tracker/
  tracker/
    context/        (propagation.py, headers.py)
    models/         (trace.py, span.py, token_event.py, token_quantity.py, enums.py)
    adapters/       (base.py, openai_responses_adapter.py, openai_chat_completions_adapter.py,
                     azure_openai_responses_adapter.py, azure_openai_chat_completions_adapter.py,
                     bedrock_converse_adapter.py, bedrock_invoke_model_adapter.py,
                     gemini_generate_content_adapter.py, anthropic_messages_adapter.py)
    normalization/  (additivity.py, reconciler.py, supersession.py, data_quality.py, normalizer.py)
    derive/         (derived_fields.py, trace_rollup.py)   # all computed totals/export fields live here
    classification/ (precision_classifier.py, unknown_reason_classifier.py)
    streaming/      (stream_tracker.py)
    estimation/     (local_tokenizer.py, historical_forecaster.py)
    workflows/      (rag_tracker.py, agent_tracker.py)
    analytics/      (coverage.py, exactness.py, anomaly_signals.py)
    export/         (csv_exporter.py, excel_exporter.py)   # materializes derived columns
    collector/      (client.py)
    storage/        (file_repository.py)                   # writes only source-of-truth fields
  api/              (main.py)
  tests/            (see per-phase tests below)
  pyproject.toml
  README.md

=====================================================================
PHASES — each ends with a STOP-and-report gate
=====================================================================
PHASE 0 — Scaffold: the structure above, pyproject.toml (pytest/Ruff config), empty modules,
  README stub. No logic. STOP.

PHASE 1 — Context propagation (HIGHEST RISK). Implement async/thread-safe propagation + cross-service headers
  (X-TokenTracker-Trace-Id, -Span-Id, -Parent-Span-Id, -Business-Id, -Workflow, -Environment, -Request-Correlation-Id).
  Tests FIRST: test_context_propagation_async.py, test_context_propagation_nested_agent.py — one root trace,
  nested spans, parallel async LLM calls, a tool call, a sub-agent span, a failed retry, a streaming span;
  every token event attaches to the correct span/trace under concurrency; unresolvable parent → flag propagation_lost.
  Do not move on until rock-solid. STOP.

PHASE 2 — Core models + enums per INV-1/INV-2. Enums: PrecisionLevel, UsageSource, UnknownReason, Additivity,
  AggregationMode (sum only in MVP; max/last reserved/unused).
  Test FIRST: test_storage_no_stored_derived_fields.py — round-trip a TokenEvent through JSONL; assert the
  read-back DERIVES included_in_total/quantity_in_total/export_warning, and assert those keys are ABSENT from
  the serialized JSON. STOP.

PHASE 3 — Additivity, derived fields, correlated supersession. Tests FIRST:
  - test_additivity_no_double_count.py: a cached+reasoning event → sum(quantity_in_total) == provider_total
    (cached/reasoning contribute 0).
  - test_event_grain_no_double_count.py: a superseded event → event_contributing_tokens == 0.
  - test_stream_supersession_no_double_count.py: interrupted-then-completed stream (partial output estimate,
    then final usage, same request_correlation_id) sums to the FINAL usage, not partial+final.
  - test_token_type_purity.py: no quantity ever uses a forbidden token_type (INV-3).
  STOP.

PHASE 4 — Adapter contract: BaseAPISurfaceAdapter + NormalizedUsage. Methods: count_input_tokens,
  extract_usage_from_response, extract_usage_from_stream_event, estimate_partial_output_tokens,
  assign_additivity, reconcile_total, classify_error. Adapters assign precision/additivity/subtotal_of;
  they do NOT compute derived fields or set supersession. STOP.

PHASE 5 — OpenAI adapters (Responses + Chat Completions). RECORDED REAL payloads as fixtures.
  Tests assert event_contributing_tokens == provider_total_tokens for a cached+reasoning response on BOTH
  surfaces, with cached_input = subtotal_of input and reasoning = subtotal_of output. STOP.

PHASE 6 — Precision per quantity + unknown-reason classifier. test_precision_per_quantity.py. STOP.

PHASE 7 — Streaming tracker: completed → exact; interrupted → output/estimate; final arrival → supersede the
  partial by request_correlation_id; timeout → output/quantity=None/unknown. test_stream_tracker.py. STOP.

PHASE 8 — Safe-failure collector: non-blocking, local buffer, batch flush, retry queue, drop policy,
  collector_timeout_ms, max_buffer_size, offline_mode. Tracker failure must NEVER raise into the caller.
  test_collector_fault_injection.py: collector down, slow, buffer full, network failure, process killed
  mid-flush, duplicate send, partial batch failure. STOP.

PHASE 9 — CSV + Excel export. Materialize quantity_in_total + export_warning into token_quantities.csv;
  event_contributing_tokens (supersession-aware, 0 if superseded) into token_events.csv; CoverageExactness sheet.
  Tests: test_export_totals_match_model.py, test_csv_excel_export.py — assert
    SUM(quantity_in_total over non-superseded) == SUM(event_contributing_tokens)
    == model trace total == CoverageExactness sheet value.
  Forbid summing raw quantity or provider_total across rows; never mix event-grain and quantity-grain in one sum.
  STOP.

PHASE 10 — Bedrock Converse + Gemini Generate Content adapters. Keep Bedrock cache fields
  additivity="unverified" (contribute 0, flag unverified_additivity) until verified against a real payload.
  Anthropic cache buckets were subsequently verified as distinct additive input quantities.
  Gemini thinking = total_contributing; reconcile to provider total. test_bedrock_converse_adapter.py,
  test_gemini_generate_content_adapter.py. STOP.

PHASE 11 — RAG + agent span helpers and tool-result token-impact tracking. RAG spans: input_preparation,
  embedding, vector_search (native metrics, not tokens), reranking, prompt_assembly, final_generation.
  Track retrieved_context_hash, retrieved_context_estimated_tokens, retrieved_context_injected_into_prompt,
  downstream_llm_span_id; agent metadata (agent_run_id, step_index, step_type, parent/sub_agent_id, tool_*,
  loop_count, max_steps_reached, retry_count, memory_read/write_count); tool_result_estimated_tokens,
  tool_result_injected_into_context, next_llm_span_id. test_rag_agent_tracking.py. STOP.

=====================================================================
ANTI-PATTERNS — if you catch yourself doing any of these, stop and fix
=====================================================================
- Serializing a derived field (quantity_in_total, included_in_total, export_warning,
  event_contributing_tokens, total_mismatch, trace totals) into JSONL.
- Adding partial_output_observed / estimated_input / estimated_output as token types.
- Summing the raw quantity column, or summing provider_total_tokens across events, to get a total.
- Leaving a superseded event with a non-zero contributing total.
- Matching supersession on span_id instead of request_correlation_id.
- Inferring additivity from the token_type string instead of the adapter setting it.
- Treating a lost/unknown output as 0 in a measured total instead of a separate count.
- Hand-writing "ideal" provider payloads instead of capturing real ones.
- Running multiple phases without stopping at the gate.

=====================================================================
DEFINITION OF DONE
=====================================================================
All tests green; Ruff clean; README documents how to run the suite, the storage/derived boundary,
and the six core falsifying tests (additivity_no_double_count, event_grain_no_double_count,
storage_no_stored_derived_fields, stream_supersession_no_double_count, export_totals_match_model,
plus the context-propagation harness). Print a final per-phase coverage summary.

Begin with PRE-FLIGHT now and STOP for my "go".
```

---

## Engineering standards (always apply)
These override any bias toward speed. Correctness first, every time.
- Test-first: write a failing test that reproduces the issue BEFORE fixing it, and confirm it fails for the right reason.
- Never claim something works without running it. Show the actual output, not a description of it.
- When a test fails, diagnose the root cause before touching code — no guess-and-check, no shotgun edits.
- Prefer the smallest correct change; call out assumptions and trade-offs explicitly.
- Before editing, read the surrounding code and match its idioms, naming, and structure.
- Respect the invariants (INV-1..INV-7) and the storage/derived boundary in every change — never serialize a derived field, never sum raw quantities.
- Run the full suite after changes (`python tests/run_all.py`) and report pass/fail honestly, including anything skipped.
- For non-trivial work: plan first, implement, then verify end-to-end and review the diff before committing.

## Notes for you (not part of the prompt)
- The gates are the safeguard. If Claude Code tries to dump the whole repo at once, hold it to "stop after each phase" — that single rule is what prevents the untested-megabuild failure mode.
- Phase 5's recorded-real-payload rule is what actually resolves the Bedrock/Anthropic additivity uncertainty. Hand-written fixtures would only reflect assumptions back at you.
- The six core tests named in Definition of Done are the falsifiers worth keeping permanently in CI; everything else can grow around them.
