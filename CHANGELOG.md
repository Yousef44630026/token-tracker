# Changelog

All notable changes to the AI Token Tracker. Dates are UTC.

## [0.4.0] - 2026-07-23 — Deployment-ready

Focus: make the product installable and operable by someone other than its author, and prove
the Azure path exhaustively.

### Added
- **`scripts/tt-deploy.ps1`** — one-command deployment orchestrator (Plan / Install / Status /
  Uninstall) composing auth, data dirs, the scheduled tasks, and Doctor verification.
- **`docs/DEPLOYMENT.md`** — single-entry operator guide: install, configure, verify, operate,
  security posture, and the proven-vs-assumed trust boundary.
- **Verified off-machine backup** (`backup_ledger.py` + scheduled task): archive-inclusive,
  lock-consistent, self-verifying (an unverified backup is quarantined), copied into OneDrive.
- **Live web dashboard** (`tracker/export/live_dashboard.py`): animates per service / provider /
  model as tokens arrive; totals reconcile to the canonical ledger.
- **Adversarial Azure test coverage**: real-stream end-to-end, cut-stream supersession, Responses
  content-filter, and a 107-check edge matrix across all Azure surfaces.
- **`provider_usage_missing`** granular flag family (a bucket present-but-absent, distinct from
  `raw_usage_missing`), wired across adapters, analytics, and dashboards.
- Provider smoke/proof tooling (Azure, Bedrock cache/stream, Vertex) and `release_readiness`,
  `scale_probe`; Ubuntu + Windows CI matrix; OpenTelemetry `gen_ai.*` projection (optional extra).

### Fixed
- Import double-count on folder/machine rename (event ids were path-derived): the import now
  dedups against the ledger by stable `(session_id, request_id)`, archive-aware.
- Silent token loss on provider schema drift: the Claude import routes through `normalize()`, so a
  renamed usage field raises `provider_schema_drift` instead of dropping tokens.
- Test isolation: `test_storage_retention` no longer rmtree's the runner-provided working dir.
- Power BI "Cache Hit Rate" now divides by the full prompt input (matches the analytics report).

### Verified
- Full suite 197 scripts + lint, green on the Windows + Ubuntu CI matrix.
- Azure OpenAI: 180+ checks on real captured payloads; `sum(counted) == provider_total` exactly.
- Live reconciliation on the real ledger: dashboard total == canonical == exports, per every axis.

## [0.3.0] - 2026-07-20 — Delivery candidate

- Schema v9: stored `overlap` × `trust`; supersession and totals derived on read, never stored.
- Bedrock cache additivity moved to documented-additive (falsifiable at first real payload).
- Anthropic cache additivity **proven** by a real recorded payload (containment falsified).
- Authenticated collector, watchdog Doctor, archive-first retention, 6-day soak evidence.
- One-line in-code integration (`track_response`); the proxy is a fallback, not a requirement.

## [0.1.0] - 2026-07-15 — First green clean-CI release

- Accounting core: per-provider additivity truth table (INV-1..INV-7), stored/derived boundary,
  correlated supersession, unknown-never-zero, the six permanent falsifiers.
- JSONL ledger, CSV/Excel/Power BI export, safe-failure collector, proxy, Claude/Codex log import.
- First fully green run on a clean CI machine.
