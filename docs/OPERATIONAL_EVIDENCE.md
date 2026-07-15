# Operational Evidence Register

This register separates code-level correctness from claims that require external evidence.
A green unit-test suite must not promote an unobserved provider or workload to "operational".

| Evidence | Current state | Pass criterion | Artifact |
|---|---|---|---|
| Permanent accounting falsifiers | Whole workflow green on clean CI (run #5, `0cd4187`, 2026-07-15) | Whole `tracker-check` workflow (all steps) green in GitHub Actions | https://github.com/Yousef44630026/token-tracker/actions/runs/29422396529 |
| Provider payload semantics | Partial — see "Provider verification" below | Real redacted capture for every supported surface and usage mode | `tracker/validation/fixture_manifest.py` + `tt-provider-matrix` |
| Billing reconciliation | Not demonstrated | Tracker totals reconciled to a provider invoice for a fixed window | Signed reconciliation summary |
| Proxy soak | Not demonstrated | 72 hours under representative streaming load with bounded memory/handles and zero silent loss | Soak report plus event store hash |
| Collector supervision | Crash recovery, alerting, stale-health dead-man, watchdog self-heal, and reboot auto-start passed; sleep/resume pending | Auto-start, restart-on-failure, downtime alert, and stale-monitor detection verified | `docs/evidence/COLLECTOR_SUPERVISION_20260714.md` |
| Collector soak | Harness and three-sample recovery proof passed; 72 hours pending | 72 hours with 100% successful probes, monotonic counters, and unchanged starting store prefix | `collector_soak` summary JSON |
| Storage substrate | Live ledger moved off sync | Live ledger resides on a non-synced local volume; exports may be synced | `tt-doctor --strict-warnings` output |
| Claude transcript importer | Canary implemented | Import report has no format-drift warnings and expected extraction ratio | `ClaudeImportReport` JSON |
| Estimator quality | Backend disclosed | `tiktoken` active or fallback explicitly accepted; error distribution measured by content class | Doctor output and estimate-vs-provider report |
| Dashboard consumption | Excel dashboard built from the live ledger (2026-07-15, 3364 events / 13361 quantity rows); scheduled+monitored refresh still pending the import/export task | Scheduled export refreshes a connected dashboard and freshness is monitored | `tt-dashboard.cmd` output `dashboard.xlsx` (manual run) |
| Retention and recovery | Drill passed on real ledger (2026-07-15, 3364 events); offsite backup rotation still an operator task | Rotation, backup, restore, and duplicate-recovery drill pass | `docs/evidence/RECOVERY_DRILL_20260715.md` |

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
| Mistral, Cohere, Voyage, Vertex AI, OpenAI/Bedrock embeddings variants | SIMULATED fixtures only (documented shape) | **Assumed** — a wrong rule would surface as `provider_total_mismatch` wherever a provider total exists, but is not proven |

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
scripts\tt-collector-soak.cmd --duration-seconds 259200 --interval-seconds 60
```

Do not store credentials, raw prompts, invoice line items, or unredacted provider responses
in this register. Link only to approved redacted artifacts.
