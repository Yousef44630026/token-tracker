# Operational Evidence Register

This register separates code-level correctness from claims that require external evidence.
A green unit-test suite must not promote an unobserved provider or workload to "operational".

| Evidence | Current state | Pass criterion | Artifact |
|---|---|---|---|
| Permanent accounting falsifiers | v9 workflow green on clean CI (`c614b1c`, 2026-07-16), after Ruff plus 170/170 isolated test scripts and 24,878 deep-fuzz assertions passed locally | Whole `tracker-check` workflow (all steps) green in GitHub Actions for the candidate commit | https://github.com/Yousef44630026/token-tracker/actions/runs/29497484410 + local `tests/run_all.py` / `tt-check` output |
| Provider payload semantics | Partial — see "Provider verification" below | Real redacted capture for every supported surface and usage mode | `tracker/validation/fixture_manifest.py` + `tt-provider-matrix` |
| Billing reconciliation | Not demonstrated | Tracker totals reconciled to a provider invoice for a fixed window | Signed reconciliation summary |
| Proxy soak | Not demonstrated | 72 hours under representative streaming load with bounded memory/handles and zero silent loss | Soak report plus event store hash |
| Collector supervision | Crash recovery, alerting, stale-health dead-man, watchdog self-heal, and reboot auto-start passed; sleep/resume pending | Auto-start, restart-on-failure, downtime alert, and stale-monitor detection verified | `docs/evidence/COLLECTOR_SUPERVISION_20260714.md` |
| Collector soak | Harness and three-sample recovery proof passed; 72 hours pending | 72 hours with 100% successful probes, monotonic counters, and unchanged starting store prefix | `collector_soak` summary JSON |
| Storage substrate | Live ledger moved off sync; strict doctor passed 16 checks with zero warnings (2026-07-16, 3,670 readable events, observed total 1,010,251,911) | Live ledger resides on a non-synced local volume; exports may be synced | `tt-doctor --strict-warnings` output |
| Claude transcript importer | Incremental authenticated task demonstrated (2026-07-16): checkpointed run scanned 16 appended lines, accepted 3 events, and newly persisted 3; format-drift and stale-task dead-men pass | Fresh scheduled JSON result, no format-drift/IO warnings, atomic checkpoint advances only after complete acknowledgement | `C:\ai-token-tracker-data\health\claude-import.log` + `tt-doctor --strict-warnings` |
| Estimator quality | Backend disclosed | `tiktoken` active or fallback explicitly accepted; error distribution measured by content class | Doctor output and estimate-vs-provider report |
| Dashboard consumption | Excel dashboard refreshed from the live ledger (2026-07-16, 3,670 events, 0 skipped/duplicate rows); absent prices report `total_cost=null`, never false zero; scheduled+monitored refresh remains pending | Scheduled export refreshes a connected dashboard and freshness is monitored | `C:\ai-token-tracker-data\dashboard.xlsx` + `tt-dashboard.cmd --json` |
| Retention and recovery | Strict drill passed on real ledger (2026-07-16, 3586 events); malformed/truncated/schema-invalid source rows now fail before backup claims; offsite rotation remains an operator task | Strict source validation, rotation, backup, restore, and duplicate-recovery drill pass | `docs/evidence/RECOVERY_DRILL_20260716.md` |

## Provider verification

`tt-provider-matrix` counts fixtures per provider/surface. That count alone understates two
providers, because what must be verified is the **additivity rule and the extraction code**, not
the label on the fixture:

| Provider | Additivity rule verified by | Status |
|---|---|---|
| Azure OpenAI (chat, responses, embeddings) | 18 recorded REAL captures (simple, cache x2, reasoning, cache+reasoning, vision, embeddings, truncated, streaming, content-filter) | Verified |
| **OpenAI (direct)** | The **same code path**: `AzureOpenAI*Adapter` subclasses `OpenAI*Adapter` and calls `super().extract_usage_from_response()`; the INV-4 table aliases `azure_openai` → `openai`. The real Azure captures therefore exercise OpenAI's extraction and its exact additivity rules (input/output contributing, `cached_input` subtotal_of input, `reasoning` subtotal_of output). | Rules verified via the shared path; only OpenAI-direct-specific wire drift (fields Azure has not shipped yet) remains unverified |
| **Anthropic Messages** | A recorded REAL capture (`anthropic_messages_cache.REAL.json`, usage verbatim, content stripped). The real turn reports `input_tokens=2` with `cache_creation_input_tokens=866255` — cache tokens cannot be contained in input, so the buckets are provably SEPARATE additive inputs. Pinned by `tests/test_real_payload_anthropic.py`. | Verified (structurally falsified, not assumed) |
| Gemini / Bedrock Converse | 1 recorded REAL capture each | Verified for the captured mode |
| **Vertex AI** | The **same code path**: `VertexAIGenerateContentAdapter` is a pure subclass of `GeminiGenerateContentAdapter` (no overrides) and the table aliases `vertex_ai` → `gemini`. The real Gemini capture exercises it. | Rules verified via the shared path |
| **Mistral** | The **same code path**: `MistralChatAdapter` is a pure subclass of `OpenAIChatCompletionsAdapter` (no overrides); its registered rules (input/output = total_contributing) are a strict subset of OpenAI's, verified by the real Azure captures. | Rules verified via the shared path; Mistral's wire format being OpenAI-compatible is assumed |
| Cohere, Voyage, embeddings variants | SIMULATED fixtures only. These have their **own** extraction (Cohere reads native `usage.tokens` / `billed_units`; Voyage reads rerank `usage.total_tokens`), so no Azure/OpenAI capture exercises them. | **Assumed** — but low-risk: they have no cache/reasoning sub-buckets, so input+output is the only coherent assignment, and a wire mismatch yields no quantities → `raw_usage_missing` (fails loud, never silent) |

Note on capturing "provider X through Azure": a capture verifies an adapter only if the payload
actually flows through THAT adapter's extraction code AND is that provider's real wire format.
Azure AI Foundry serves Mistral/Cohere on an OpenAI-compatible endpoint, so such a capture
exercises `AzureOpenAIChatCompletionsAdapter` — not `CohereChatAdapter` — and proves nothing about
the native adapter. Conversely, if a provider is consumed ONLY through Azure, its native adapter is
not on the deployment's path at all and should be marked experimental rather than "verified".

The two genuinely hazardous rules were the cache-containment ones, which disagree between vendors
and could each have been silently wrong: OpenAI's `cached_input` IS contained in input (subtotal),
Anthropic's cache buckets are NOT (separate additive). Both are now proven against real payloads.

Reconciliation identity: across every fixture that carries a provider total,
`sum(quantity_in_total) == provider_total_tokens` holds exactly (no mismatch, no silent hole).
Anthropic reports no total, which is why its rule needed the structural falsification above.

## Release Rule

Code readiness and operational readiness are separate statuses. A release may claim the
accounting core is validated when CI passes. It may claim a provider surface is operational
only when its real fixture, soak/reliability evidence, and storage path are all present.

## Immediate Operator Commands

```powershell
scripts\tt-doctor.cmd --store C:\ai-token-tracker-data\collector_events.jsonl --strict-warnings
scripts\tt-check.cmd
scripts\tt-verify.cmd
scripts\tt-claude-import-task.ps1 -Mode Status
scripts\tt-collector-soak.cmd --duration-seconds 259200 --interval-seconds 60
```

Do not store credentials, raw prompts, invoice line items, or unredacted provider responses
in this register. Link only to approved redacted artifacts.
