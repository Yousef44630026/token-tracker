# Tracker Test Guide

This guide is a step-by-step validation plan for the tracker. It is intentionally not
generic: every step names the tracker feature being tested, the concrete command to run,
the expected result, and the reason the test matters.

Use the portable Python when available:

```cmd
set PY=C:\Users\yerabhaoui\python-portable\python.exe
```

If that path is not available on another machine, replace `%PY%` with `python`.

## Step 0 - Trusted Smoke Gate

Goal: prove the trusted core still passes before deeper testing.

Command:

```cmd
scripts\tt-verify.cmd
```

Expected result:

- All listed tests print `RESULT: all checks passed`.
- Final line prints `TRUSTED VERIFICATION: PASS`.
- Provider matrix prints `overall_status: warn`.
- Provider matrix prints `pass/warn/fail: 1/14/0`.

What this proves:

- Core model totals still reconcile.
- Realistic fixture audit is complete.
- Operational metrics, exports, Power BI export, Azure real payload, and provider matrix still work.
- No registered provider surface is adapter-only.

## Step 1 - Core Token Model Invariants

Goal: verify the source-of-truth model cannot store incorrect derived totals.

Commands:

```cmd
%PY% tests\test_token_quantity.py
%PY% tests\test_token_quantity_serialization.py
%PY% tests\test_token_type_purity.py
%PY% tests\test_derived_fields.py
%PY% tests\test_storage_no_stored_derived_fields.py
```

Expected result:

- Unknown quantities require `PrecisionLevel.UNKNOWN`.
- Negative quantities are rejected.
- `quantity_in_total` is derived, not serialized.
- `event_contributing_tokens` is derived, not serialized.
- Token type never encodes measurement quality such as estimated/partial.

What this proves:

- The tracker cannot persist stale totals.
- Counting rules are enforced at the model level before adapters, exports, or dashboards.

## Step 2 - Additivity and Double-Counting Rules

Goal: verify cache, reasoning, thinking, and subtotal fields do not inflate totals.

Commands:

```cmd
%PY% tests\test_additivity_table.py
%PY% tests\test_additivity_no_double_count.py
%PY% tests\test_double_count_guard.py
%PY% tests\test_event_grain_no_double_count.py
%PY% tests\test_export_totals_match_model.py
```

Expected result:

- `total_contributing` quantities contribute to totals.
- `subtotal_of` quantities contribute 0 to totals.
- `unverified` quantities contribute 0 and surface warnings.
- Event-grain total and quantity-grain total agree.
- Raw provider totals and raw quantities are not used as business totals.

What this proves:

- OpenAI/Azure cached input and reasoning subtotals cannot be counted twice.
- Gemini thinking and cached content cannot inflate totals.
- Bedrock cache read/write remain separate additive input buckets.

## Step 3 - Trace, Span, and Supersession Behavior

Goal: verify multi-step workflows are tracked without duplicates or stale stream totals.

Commands:

```cmd
%PY% tests\test_trace_span.py
%PY% tests\test_trace_serialization.py
%PY% tests\test_trace_rollup.py
%PY% tests\test_trace_repository.py
%PY% tests\test_supersession_edge_cases.py
%PY% tests\test_stream_supersession_no_double_count.py
```

Expected result:

- A trace rejects duplicate event IDs and duplicate span IDs.
- Trace serialization round-trips without storing derived totals.
- Superseded partial stream events contribute 0.
- Final provider usage event replaces partial stream estimates.
- Rollups sum only authoritative, non-superseded event totals.

What this proves:

- Streaming and retries cannot duplicate usage.
- The trace remains a reliable source for BI and reports.

## Step 4 - Context Propagation and Correlation

Goal: verify event identity follows nested, async, and header-based workflows.

Commands:

```cmd
%PY% tests\test_context_core.py
%PY% tests\test_context_identity.py
%PY% tests\test_context_headers.py
%PY% tests\test_headers_roundtrip.py
%PY% tests\test_headers_blank_identity_regression.py
%PY% tests\test_context_propagation_async.py
%PY% tests\test_context_propagation_nested_agent.py
%PY% tests\test_concurrency_context.py
```

Expected result:

- Trace IDs and span IDs are stable through nested calls.
- Parent span IDs are preserved.
- HTTP headers round-trip context identity.
- Blank identity headers are rejected or regenerated safely.
- Async tasks do not leak context into unrelated tasks.

What this proves:

- A token event can be attributed to the correct request, service, span, and agent step.

## Step 5 - Adapter Contract and Registry Coverage

Goal: verify every adapter follows the same normalization contract and is registered.

Commands:

```cmd
%PY% tests\test_adapter_contract.py
%PY% tests\test_adapter_methods.py
%PY% tests\test_adapter_registry.py
%PY% tests\test_registry_completeness.py
%PY% tests\test_adapter_stream_all.py
```

Expected result:

- Every adapter exposes provider and API surface identity.
- `create_adapter(provider, surface)` works for registered adapters.
- New adapter classes cannot exist silently outside the registry.
- Stream extraction methods return final usage only when the provider supports it.

What this proves:

- The tracker has a consistent plug-in surface for providers.
- Provider matrix rows match actual adapter availability.

## Step 6 - Provider-Specific Usage Normalization

Goal: verify each provider surface maps real provider usage into tracker quantities correctly.

Commands:

```cmd
%PY% tests\test_openai_adapters.py
%PY% tests\test_azure_openai_adapters.py
%PY% tests\test_embeddings_adapter.py
%PY% tests\test_anthropic_messages_adapter.py
%PY% tests\test_bedrock_converse_adapter.py
%PY% tests\test_bedrock_invoke_model_adapter.py
%PY% tests\test_gemini_generate_content_adapter.py
%PY% tests\test_vertex_and_bedrock_embeddings.py
%PY% tests\test_more_providers.py
```

Expected result:

- OpenAI Responses and Chat extract input, output, cached input, reasoning, audio, and totals.
- Azure OpenAI preserves the Azure provider label and deployment metadata.
- Azure embeddings use the OpenAI embeddings usage shape but report provider `azure_openai`.
- Anthropic cache creation/read fields are separate contributing input buckets.
- Bedrock Converse extracts non-cached input, cache read/write, and output as additive buckets.
- Bedrock InvokeModel handles multiple body families.
- Gemini and Vertex AI extract prompt, candidates, cached content, thinking, and total.
- Cohere, Mistral, and Voyage surfaces normalize their usage without fabricated totals.

What this proves:

- Provider-specific semantics are implemented intentionally, not treated as one generic schema.

## Step 7 - Realistic Fixture Audit

Goal: verify every realistic payload fixture is mapped to exactly one adapter and reconciles.

Commands:

```cmd
%PY% tests\test_reconciliation_audit.py
%PY% tests\test_realistic_payloads.py
%PY% tests\test_real_payload_azure.py
%PY% tests\test_real_payload_bedrock.py
%PY% tests\test_real_payload_gemini.py
```

Expected result:

- Every `*.SIMULATED.json` and `*.REAL.json` fixture has an explicit adapter mapping.
- Every fixture normalizes without negative quantities.
- `sum(quantity_in_total)` equals `event_contributing_tokens`.
- Provider totals reconcile when the provider reports a total.
- Real Azure, Bedrock, and Gemini payloads remain marked as real evidence.

What this proves:

- The tracker is tested against realistic provider shapes, not only toy dictionaries.
- Fixture coverage supports the provider validation matrix.

## Step 8 - Provider Matrix and Trust Reporting

Goal: verify the project honestly reports validation coverage.

Commands:

```cmd
scripts\tt-provider-matrix.cmd
scripts\tt-provider-matrix.cmd --json --output provider_matrix.json
%PY% tests\test_trust_reporting.py
```

Expected result:

- Matrix reports 15 surfaces.
- `fail_count` is 0.
- `adapter_only_surface_count` is 0.
- Current overall status is `warn`, not `pass`, because real and stream gaps remain.
- HTML report includes readiness overview, provider matrix, observation contract, service attribution, and anomalies.

What this proves:

- The project is transparent about what is real-validated, simulated-only, or still missing.

## Step 9 - Stream Handling

Goal: verify streamed calls are counted from final usage when possible and from estimates only when necessary.

Commands:

```cmd
%PY% tests\test_stream_consumer.py
%PY% tests\test_stream_tracker.py
%PY% tests\test_stream_tracker_more.py
%PY% tests\test_stream_supersession_no_double_count.py
%PY% tests\test_scenario_c_stream_interrupted.py
```

Expected result:

- Final stream usage supersedes partial stream estimates.
- Interrupted streams surface unknown or estimated precision.
- Superseded partial events contribute 0.
- Stream timeouts and interruptions produce explicit quality flags.

What this proves:

- Streaming does not overcount tokens.
- Partial observations are visible but not mixed with exact provider totals.

## Step 10 - Collector and Delivery Hardening

Goal: verify the collector handles online, offline, timeout, inflight, concurrency, and partial delivery cases.

Commands:

```cmd
%PY% tests\test_collector_config.py
%PY% tests\test_collector_functional.py
%PY% tests\test_collector_fault_injection.py
%PY% tests\test_collector_inflight.py
%PY% tests\test_concurrency_collector.py
%PY% tests\test_delivery_hardening.py
%PY% tests\test_api_collector.py
%PY% tests\test_api_auth_and_errors.py
%PY% tests\test_api_server_errors.py
```

Expected result:

- Collector offline mode reports offline, not success.
- Timeouts are reported as failures.
- Partial failures remain observable.
- Concurrent submissions do not corrupt state.
- API auth and error paths return explicit failure responses.

What this proves:

- Events are not silently dropped or falsely marked as delivered.

## Step 11 - Storage and Loading

Goal: verify JSONL storage, recovery, and load/export paths preserve source-of-truth semantics.

Commands:

```cmd
%PY% tests\test_file_repository.py
%PY% tests\test_load_storage.py
%PY% tests\test_load_events.py
%PY% tests\test_load_collector.py
%PY% tests\test_end_to_end_pipeline.py
```

Expected result:

- JSONL repository appends and reads events.
- Duplicate IDs are not appended twice when using append-unique behavior.
- Truncated tail recovery is handled.
- Loaded traces still derive totals instead of reading stored totals.
- End-to-end pipeline exports consistent totals.

What this proves:

- Stored events remain safe for replay, BI export, and audit.

## Step 12 - Operational Metrics

Goal: verify derived metrics are correct and stay separate from pricing.

Commands:

```cmd
%PY% tests\test_operational_metrics.py
%PY% tests\test_reliability_unmeasured_regression.py
%PY% tests\test_service_attribution_cloud_regression.py
%PY% tests\test_cache_fresh_tokens_regression.py
%PY% tests\test_agent_metrics_regression.py
```

Expected result:

- Latency excludes non-authoritative error events.
- Reliability sees all events and counts errors, retries, rate limits, and missing usage.
- Cache hit rate uses the prompt input denominator.
- RAG summary links retrieved context to downstream LLM input.
- Agent summary counts runs, tool calls, retries, and agent-attached token totals.
- Service attribution preserves service, tenant, cloud, region, provider, model, deployment.
- No cost or pricing summary is written.

What this proves:

- Dashboards can use tracker metrics without recomputing fragile logic.

## Step 13 - CSV, Excel, HTML, and Power BI Exports

Goal: verify every export materializes safe derived columns and never reintroduces pricing.

Commands:

```cmd
%PY% tests\test_csv_excel_export.py
%PY% tests\test_export_totals_match_model.py
%PY% tests\test_html_report_escaping_regression.py
%PY% tests\test_powerbi_export.py
%PY% tests\test_powerbi_exporter_regression.py
```

Expected result:

- Excel has `TokenQuantities`, `TokenEvents`, `TokenSpans`, and `CoverageExactness`.
- CSV exports include operational summaries.
- `quantity_in_total` and `event_contributing_tokens` match the model total.
- HTML report escapes unsafe text.
- Power BI export writes fact tables, dimensions, `manifest.json`, `measures.dax`, and `README.md`.
- Power BI manifest says never to sum `provider_total_tokens` or raw `quantity`.
- No pricing or cost fields exist in export outputs.

What this proves:

- Excel and Power BI dashboards cannot accidentally rely on stale or unsafe totals.

## Step 14 - Privacy, Prompt Suites, and Proxy Reporting

Goal: verify prompt tracking works without leaking raw sensitive content.

Commands:

```cmd
%PY% tests\test_privacy_audit.py
%PY% tests\test_privacy_audit_severity_regression.py
%PY% tests\test_prompt_suite.py
%PY% tests\test_proxy_report.py
%PY% tests\test_real_call_proxy.py
```

Expected result:

- Privacy audit finds obvious raw prompts or credential-like leakage.
- Severity classification remains stable.
- Prompt suite dry-run works without provider calls.
- Prompt suite quality checks can evaluate child stdout without storing it.
- Proxy report summarizes exact usage, incomplete events, provider IDs, statuses, and prompt groups.

What this proves:

- The real-call workflow can be audited without making raw prompt leakage invisible.

## Step 15 - Codex and Local Log Tracking

Goal: verify local Codex token-count imports and prompt-suite tracking.

Commands:

```cmd
%PY% tests\test_codex_local_tracking.py
%PY% tests\test_claude_code_logs.py
%PY% tests\test_live_usage.py
```

Expected result:

- Codex local session token-count events are imported once.
- Existing session filtering works.
- Claude Code log parsing extracts usage without duplicate imports.
- Live usage bars update from imported events.

What this proves:

- The tracker can observe local AI tool usage, not only HTTP proxy calls.

## Step 16 - RAG, Agent, and Multi-Step Scenarios

Goal: verify realistic workflows across retrieval, agents, tools, streams, failover, and duplicate delivery.

Commands:

```cmd
%PY% tests\test_scenario_a_rag_conversation.py
%PY% tests\test_scenario_b_agent_tool_calls.py
%PY% tests\test_scenario_c_stream_interrupted.py
%PY% tests\test_scenario_d_cross_provider_failover.py
%PY% tests\test_scenario_e_duplicate_delivery.py
%PY% tests\test_rag_agent_tracking.py
%PY% tests\test_rag_spans_all.py
%PY% tests\test_rag_multimodel_deep.py
%PY% tests\test_agent_bedrock_deep.py
```

Expected result:

- RAG vector search spans are counted.
- Prompt assembly metadata links retrieved context to downstream LLM span.
- Agent steps count retries, loops, memory reads/writes, and tool calls.
- Tool result estimated tokens are visible but not treated as provider token totals.
- Cross-provider failover attributes usage and failures to the correct provider.
- Duplicate delivery does not double-count events.

What this proves:

- The tracker works for actual AI workflows, not just isolated provider calls.

## Step 17 - Robustness and Fuzz Tests

Goal: verify malformed payloads and edge values fail closed.

Commands:

```cmd
%PY% tests\test_robustness_malformed.py
%PY% tests\test_robustness_values.py
%PY% tests\test_random_integration_fuzz.py
%PY% tests\test_azure_limits_fuzz.py
%PY% tests\test_precision_classifier_edges.py
%PY% tests\test_precision_per_quantity.py
%PY% tests\test_data_quality_flags.py
%PY% tests\test_anomaly_signals.py
%PY% tests\test_version_drift.py
```

Expected result:

- Missing usage raises explicit flags.
- Renamed provider fields do not fabricate missing token counts.
- Malformed values do not create negative or invalid totals.
- Precision is tracked per quantity.
- Version drift and anomaly signals are surfaced.

What this proves:

- The tracker remains conservative when providers change response shapes.

## Step 18 - Local Tokenizer and Historical Estimates

Goal: verify estimate paths remain visibly separate from exact provider usage.

Commands:

```cmd
%PY% tests\test_local_tokenizer.py
%PY% tests\test_historical_forecaster.py
```

Expected result:

- Local token estimates are marked with estimate precision and local source.
- Historical forecast values are not confused with exact provider response usage.

What this proves:

- Estimated tokens remain useful for budgets and planning but cannot masquerade as exact usage.

## Step 19 - Optional Live Provider Tests

Goal: verify the proxy path against live providers when credentials and network access are available.

Commands:

```cmd
%PY% tests\live\test_live_openai.py
%PY% tests\live\test_live_openai_proxy.py
```

Expected result:

- Live calls are skipped or fail clearly when credentials/network/quota are unavailable.
- When configured, live OpenAI usage is captured and reconciled.
- Proxy path writes JSONL events and privacy checks pass.

What this proves:

- The local simulated and real fixture behavior still matches a live provider path.

Important:

- Do not run live tests during normal local verification unless credentials and budget are intentionally available.
- Live tests can be unstable because of network, quota, and provider-side changes.

## Step 20 - Manual Power BI Dashboard Validation

Goal: verify the BI integration can support the dashboard design.

Commands:

```cmd
scripts\tt-powerbi-export.cmd --store codex_events.jsonl --output powerbi_dataset
```

Manual checks:

- `powerbi_dataset\fact_token_events.csv` exists.
- `powerbi_dataset\fact_token_quantities.csv` exists.
- `powerbi_dataset\fact_service_daily.csv` exists.
- `powerbi_dataset\provider_validation_matrix.csv` exists.
- `powerbi_dataset\manifest.json` exists.
- `powerbi_dataset\measures.dax` exists.
- `powerbi_dataset\README.md` exists.

Power BI checks:

- Import each CSV as a table.
- Create the relationships listed in `manifest.json`.
- Add measures from `measures.dax`.
- Confirm `Total Contributing Tokens` equals the sum of `fact_token_events[event_contributing_tokens]`.
- Confirm `provider_total_tokens` is not used as a global measure.
- Confirm Trust Readiness page shows `fail_count = 0` and `overall_status = warn`.

What this proves:

- The dashboard design can be implemented on top of the exported model without rebuilding tracker logic inside Power BI.

## Step 21 - Release Acceptance Criteria

A release is acceptable when:

- `scripts\tt-verify.cmd` passes.
- `provider-matrix` has `fail_count = 0`.
- Every new realistic fixture is present in `REALISTIC_FIXTURE_ADAPTERS`.
- Every new adapter class is registered.
- Any new metric has at least one targeted test and one export assertion.
- Any new provider behavior has a fixture and a reconciliation test.
- Any new dashboard field is present in Power BI export tests.
- No pricing or cost logic is reintroduced.

For a stricter release, also run:

```cmd
%PY% tests\run_all.py --skip-lint
```

For the strictest local release, run:

```cmd
%PY% tests\run_all.py
```

The strictest run reports a visible lint skip if Ruff is not installed in the current interpreter.

## Current Known Validation Gaps

The current project status is expected to be warning-only, not perfect:

- Most provider surfaces still need more real captures.
- Streaming fixtures are still missing for several stream-capable surfaces.
- Azure Responses has real fixtures but no simulated fixture.
- Some Bedrock, OpenAI, Anthropic, Cohere, Mistral, Vertex, and Voyage surfaces are simulated-only.

These are evidence gaps, not core counting architecture failures.
