# Codex Agent Playbook

These are project-scoped Codex custom agents for the token tracker. Each `*.toml` file is a
spawnable read-only specialist pinned to `gpt-5.6-sol` with `ultra` reasoning; the matching
Markdown file is its detailed domain playbook. They complement Claude's agents without
changing the tracker's application runtime or Azure Foundry deployment configuration.

The goal is not "many agents". The goal is a repeatable expert review system where every
domain has:

- hard invariants
- adversarial pressure tests
- a minimum evidence contract
- an excellence scorecard
- clear handoff rules when a problem crosses layer boundaries

## Ground Rules

- Never run Black in this project. Use Ruff and the plain script tests.
- Use `C:\Users\yerabhaoui\python-portable\python.exe` when running Python directly.
- Do not stage, commit, push, reset, or revert user changes unless explicitly asked.
- Treat pasted credentials as exposed. Do not print secrets; rotate leaked keys.
- Storage is source of truth. Derived totals are computed only.
- Unknown is not zero. Rejected/skipped data must be counted or dead-lettered.
- Provider totals are reconciliation facts, not additive facts.
- Prefer a small failing regression test before a fix when changing behavior.
- Run only the checks needed for confidence, then the project gate for release readiness.

## Agents

| Agent | Use When | Owns |
| --- | --- | --- |
| `core-accounting-auditor` | Model, additivity, observation authority, supersession | `tracker/models`, `tracker/normalization`, `tracker/derive` |
| `provider-surface-auditor` | Azure/OpenAI/Bedrock/Gemini/etc. adapter behavior | `tracker/adapters`, provider fixtures |
| `trace-stream-guardian` | Context propagation, streams, retries, partial usage | `tracker/context`, `tracker/streaming` |
| `storage-collector-warden` | Collector, JSONL repository, proxy delivery, dead-letter | `api`, `tracker/storage`, `tracker/collector`, `tracker/proxy` |
| `analytics-export-auditor` | TrustReport, metrics, CSV/Excel/Power BI parity | `tracker/analytics`, `tracker/export` |
| `ops-release-verifier` | Doctor, Azure smoke, CI, docs, local release readiness | `tracker/ops`, `scripts`, `.github`, docs |
| `domain-scorecards` | Evidence-based rating after specialist reviews | Cross-domain scores and prioritized gaps |

Codex loads the agents from `.codex/agents/*.toml` when the project is trusted. The shared
`.codex/config.toml` allows one complete seven-specialist pass, prevents recursive fan-out,
and leaves implementation to the parent task. A typical request is:

```text
Review operational readiness with core_accounting_auditor,
storage_collector_warden, and ops_release_verifier in parallel. Wait for all
three, then consolidate only evidence-backed findings.
```

## Shared Protocol

Read [operating-protocol.md](operating-protocol.md) before using any role for a substantial
change. It defines the common workflow: route, inspect, falsify, patch, verify, and report.

Use [domain-scorecards.md](domain-scorecards.md) to rate whether a layer is merely passing
tests or genuinely strong.

## Routing Rules

- If the change can affect a total, use `core-accounting-auditor`.
- If the change interprets a provider payload, use `provider-surface-auditor`.
- If the change touches async, threads, streams, retries, or correlation, use `trace-stream-guardian`.
- If the change touches append, delivery, collector API, rejected data, or proxy storage, use `storage-collector-warden`.
- If the change touches reports, metrics, TrustReport, CSV, Excel, or Power BI, use `analytics-export-auditor`.
- If the change touches scripts, CI, Azure smoke, docs, release readiness, or secrets, use `ops-release-verifier`.

When two agents disagree, the invariant owner wins:

- Totals and authority: `core-accounting-auditor`
- Provider semantics: `provider-surface-auditor`
- Delivery loss: `storage-collector-warden`
- Export parity: `analytics-export-auditor`
- Operational safety: `ops-release-verifier`

## Output Contract

Every serious agent pass should end with:

- verdict: pass, fixed, blocked, or needs evidence
- files inspected and files changed
- invariant risks considered
- tests run with actual pass/fail result
- remaining risk, if any

## Default Verification

Use the smallest relevant test first, then the project gate:

```powershell
scripts\tt-check.cmd
scripts\tt-doctor.cmd --skip-store
```

Live Azure/Foundry calls are opt-in only:

```powershell
scripts\tt-azure-smoke.cmd --dry-run --json
scripts\tt-azure-smoke.cmd --require-live
```
