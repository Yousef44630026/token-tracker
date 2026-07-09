# Layer agents

Specialized subagents, each owning one layer of the tracker and carrying that layer's binding
invariants, file map, playbooks, and known traps. Invoke by name, or let the harness
auto-delegate from each agent's `description`. Each spawn starts fresh — hand it the task and
the relevant file paths; it already knows the environment and the rules.

All agents run on **opus**. Every module in `tracker/` has exactly one owner.

| Agent | Owns | Guards |
|-------|------|--------|
| `core-model-guardian` | `models/`, `normalization/`, `derive/`, `classification/`, `observability/` | INV-1..INV-7, storage/derived boundary, additivity axes (overlap × trust), supersession, observation contract |
| `adapter-specialist` | `adapters/` (18 adapters + registry), `validation/fixture_manifest.py`, `tests/fixtures/` | Per-provider additivity table (INV-4), token_type purity (INV-3), recorded-real-payload rule, real-vs-simulated honesty |
| `context-streaming-engineer` | `context/`, `streaming/`, `workflows/`, `estimation/` | Async/thread-safe propagation, correlation-id supersession (INV-5), stream state machine, span-tree attribution — the declared highest-risk layer |
| `collector-storage-engineer` | `api/`, `collector/`, `storage/`, `proxy/` | Safe-failure delivery (never raises into the caller), no silent loss (ack/requeue/dead-letter/count), JSONL boundary, crash recovery, privacy (no raw prompts/credentials) |
| `analytics-export-engineer` | `analytics/` (14 modules), `export/` (CSV/Excel/Power BI/HTML) | Reconciliation identity (export == model), grain discipline, coverage-vs-exactness axes, lower-bound honesty |
| `qa-test-runner` | cross-cutting | Test-first red-green, the suite + ruff/black gate, the six falsifiers, flake-vs-regression triage, honest verdicts |

## Division of labor
- A layer agent **redesigns** its layer (test-first). `qa-test-runner` **verifies**, triages a
  failure to its root cause, and hands off to the owning layer with evidence — it does not redesign.
- Cross-layer changes belong to the layer whose invariant is at stake: what a quantity
  *contributes* → `core-model-guardian`; a provider payload's *shape* → `adapter-specialist`;
  *where an event attaches* → `context-streaming-engineer`; whether an event *survives transit
  and disk* → `collector-storage-engineer`; whether a *reported number reconciles* →
  `analytics-export-engineer`.

## Shared ground rules (each agent's file restates its own)
- Tests run under the portable interpreter (not on PATH):
  `C:\Users\yerabhaoui\python-portable\python.exe tests\run_all.py` (includes the ruff+black
  lint gate). pytest is not installed. `tests/live/` costs real money — only with explicit ask.
- Never install software. Never fabricate provider payloads, token counts, or billing artifacts.
  No pricing logic, no database, no observability SDKs.
- OneDrive-synced folder: a concurrent session can edit files live, and cwd-writing tests can
  flake under file locks in batch runs. Run `git status` before committing; surface unexplained
  drift instead of clobbering it.
