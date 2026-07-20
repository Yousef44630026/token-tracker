# Verification Report - Token Counting Audit

Scope: provider token categorization, additivity, reconciliation, streaming completion,
supersession, schema-drift detection, export/reporting, and operational release gating.
The audit includes production code changes. A green code suite is not treated as proof of a
cloud capability: only a provider payload marked `REAL` can certify that capability.

## Verification Matrix

| provider | token_type | additivity | contributes? | verified-by |
|---|---|---:|---:|---|
| openai | input | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| openai | output | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| openai | cached_input | subtotal_of input | no | test_categorization_matrix.py; test_double_count_guard.py |
| openai | reasoning | subtotal_of output | no | test_categorization_matrix.py; test_double_count_guard.py |
| openai | embedding | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| openai | audio_input | subtotal_of input | no | test_categorization_matrix.py; test_double_count_guard.py |
| openai | audio_output | subtotal_of output | no | test_categorization_matrix.py; test_double_count_guard.py |
| azure_openai | input/output/cache/reasoning/audio/embedding | aliases openai table | per openai | test_categorization_matrix.py; test_categorization_completeness.py |
| gemini | input | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | output | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | cached_input | subtotal_of input | no | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | thinking | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | image_input | subtotal_of input | no | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | audio_input | subtotal_of input | no | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | video_input | subtotal_of input | no | test_categorization_matrix.py; test_reconciliation_audit.py |
| gemini | audio_output | subtotal_of output | no | test_categorization_matrix.py; test_reconciliation_audit.py |
| vertex_ai | input/output/cache/thinking/modalities | aliases gemini table | per gemini | test_categorization_matrix.py; test_categorization_completeness.py |
| anthropic | input | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| anthropic | output | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| anthropic | cached_input | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| anthropic | cache_creation_input | total_contributing | yes | test_categorization_matrix.py; test_double_count_guard.py |
| bedrock | input | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| bedrock | output | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| bedrock | cached_input | total_contributing | yes | test_categorization_matrix.py; test_bedrock_converse_adapter.py |
| bedrock | cache_creation_input | total_contributing | yes | test_categorization_matrix.py; test_bedrock_converse_adapter.py |
| bedrock | embedding | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| mistral | input | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| mistral | output | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| cohere | input | total_contributing | yes | test_categorization_matrix.py; test_categorization_completeness.py |
| cohere | output | total_contributing | yes | test_categorization_matrix.py; test_categorization_completeness.py |
| voyage | rerank_input | total_contributing | yes | test_categorization_matrix.py; test_reconciliation_audit.py |
| unregistered provider/type | any unlisted token_type | unverified | no | test_categorization_matrix.py |

## Counting Rules Verified

| rule | evidence |
|---|---|
| Total is sum(quantity_in_total), not raw quantity sum | test_double_count_guard.py builds crowded OpenAI and Anthropic events and checks derived totals exactly. |
| subtotal_of contributes 0 | test_categorization_matrix.py and test_reconciliation_audit.py check every subtotal quantity contributes 0. |
| total_contributing contributes its quantity | test_categorization_matrix.py checks representative quantities; reconciliation fixtures check event totals. |
| unverified contributes 0 and flags | test_categorization_matrix.py checks an unregistered provider/type and normalizer flag behavior. |
| provider totals reconcile to derived totals | test_reconciliation_audit.py discovers every realistic *.SIMULATED.json and *.REAL.json fixture and checks event_total_mismatch is 0 or None. |
| drift is flagged, not silent | test_reconciliation_audit.py includes renamed/dropped OpenAI usage-field checks for provider_total_mismatch/raw_usage_missing. |
| documented fields are mapped or explicitly ignored | test_categorization_completeness.py lists each adapter usage field and proves it maps to a TokenType, provider total, metadata, or ALLOWED_IGNORED reason. |

## Test Results

Focused audit tests:

| test | passing checks | failures |
|---|---:|---:|
| tests/test_categorization_matrix.py | 48 | 0 |
| tests/test_reconciliation_audit.py | 94 | 0 |
| tests/test_categorization_completeness.py | 208 | 0 |
| tests/test_double_count_guard.py | 17 | 0 |

Total focused audit checks: 367 passing, 0 failing.

Full suite on 2026-07-19:

```text
Executed 187 test scripts + lint gate; failures: 0
TRACKER CHECK: PASS
```

Command: `scripts\tt-check.cmd --lint-timeout-seconds 120 --test-timeout-seconds 180`.
The runner scans the five Python source roots only, isolates every script in a temporary
workspace, checks debris after each script, and fails closed on lint or test timeout.

## Gaps Found

| gap | real library bug? | fixed? | evidence |
|---|---:|---:|---|
| Completeness-test model treated Mistral as if OpenAI detail subfields were documented. | no | yes, test fixture narrowed to documented Mistral usage shape | test_categorization_completeness.py passes |
| Completeness-test model initially did not separate quantity fields from genuine raw response totals. Voyage rerank total_tokens is both; Bedrock and Vertex embedding counters are quantity-only. | no | yes, separated quantity fields from raw provider totals | test_categorization_completeness.py passes |
| A test or repository-wide lint process could hang the gate indefinitely. | process-integrity bug | yes, both phases now have explicit timeouts and lint scans only Python roots | test_run_all_cleanup.py; full-suite pass above |
| Azure Responses streaming lacks a REAL terminal-usage fixture. | evidence gap | no code-only fix | `tt-release-gate.cmd` fails the exact capability requirement |
| Vertex generate/cache/stream and embeddings remain simulated or unvalidated. | evidence gap | no code-only fix | capability certification matrix and release gate |
| Bedrock cache/stream/InvokeModel/embedding claims remain partly simulated or unvalidated. | evidence gap | no code-only fix | capability certification matrix and release gate |
| Runtime pricing, latency, and provider-total coverage are currently 0%. | data/configuration gap | no code-only fix | dashboard evidence reports `quality_status=warning` |
