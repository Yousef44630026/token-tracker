# Deployment guide

The single entry point for deploying and operating the AI Token Tracker. Other docs go deeper;
start here.

## What this deploys, and its honest scope

A local token-accounting stack: a loopback **collector** (authenticated JSONL ledger), scheduled
**tasks** (usage import, dashboard refresh, verified backup, health watchdog, monitor), and two
**dashboards** (live web + Excel). It tracks token usage per service, exactly, and proves it.

Supported deployment target today: **a single Windows user account.** The four data-plane tasks
install without admin; the Collector and Monitor use an at-startup trigger and need one elevated
step. Provider capture status (what is *proven*, not assumed) lives in
[OPERATIONAL_EVIDENCE.md](OPERATIONAL_EVIDENCE.md) — **Azure OpenAI is verified for production;
Bedrock/Vertex are gated** until a real cloud capture proves them.

**Volume ceiling (measured).** The single-file JSONL design is built for a personal / single-team
ledger, not a high-volume fleet. Every dashboard and `/v1/stats` poll re-projects the whole ledger,
so the live surfaces stay responsive to roughly **50,000 events** and the live dashboard's 2s poll
keeps up to roughly **5,000**; past ~50k a read spills through a temp SQLite round-trip and takes
tens of seconds to minutes. Beyond that you need a materialized rollup or the partitioned repository
with stored totals — see the **Scale envelope** row in
[OPERATIONAL_EVIDENCE.md](OPERATIONAL_EVIDENCE.md). Rotate/archive to keep the active file small.

## 1. Prerequisites

- Windows 10/11, Python 3.11+ (a portable Python is fine; it need not be on PATH).
- A **non-synced local volume** for the ledger (default `C:\ai-token-tracker-data`). Do NOT put
  the live ledger on OneDrive/Dropbox — sync engines fork append-only files. Backups may sync.

## 2. Install the package

From the repo root, into your service's / operator's Python environment:
```
pip install -e .
```
Verify the CLIs resolve: `ai-token-tracker-doctor --help`.

## 3. Bring the stack up (one command)

```
powershell -ExecutionPolicy Bypass -File scripts\tt-deploy.ps1 -Mode Plan      # dry run: see what it will do
powershell -ExecutionPolicy Bypass -File scripts\tt-deploy.ps1 -Mode Install   # deploy
```
`Install` creates the data/config/health dirs, generates an **ACL-restricted auth token outside
the repo**, installs the four standard-user tasks, and verifies with the Doctor. It is idempotent.

The Collector and Monitor need an **admin** shell (at-startup trigger). `Install` prints the exact
commands; run them once elevated:
```
powershell -ExecutionPolicy Bypass -File scripts\tt-collector-task.ps1 -Mode Install
powershell -ExecutionPolicy Bypass -File scripts\tt-collector-monitor-task.ps1 -Mode Install
```

## 4. Configuration reference (environment)

| Variable | Purpose | Default |
|---|---|---|
| `TRACKER_STORE` | live ledger path (non-synced) | `C:\ai-token-tracker-data\collector_events.jsonl` |
| `TRACKER_AUTH_TOKEN` / `TRACKER_AUTH_TOKEN_FILE` | collector bearer (env or ACL file) | file under `<data>\config\` |
| `TRACKER_HOST` / `TRACKER_PORT` | collector bind | `127.0.0.1` / `8787` |
| `TRACKER_BACKUP_DIR` | off-machine backup target | a OneDrive folder (copies may sync) |
| `AI_TOKEN_TRACKER_PYTHON` | pin the interpreter for `.cmd` wrappers | resolved by `scripts\_python.cmd` |
| `AZURE_OPENAI_API_KEY` / `_ENDPOINT` / `_DEPLOYMENT` | for the real Azure smoke | — |

## 5. Verify the deployment

```
scripts\tt-doctor.cmd --store %TRACKER_STORE% --strict-warnings   # stack health, must be green
scripts\tt-provider-matrix.cmd                                    # which surfaces are verified
scripts\tt-azure-smoke.cmd --json                                 # real Azure calls, reconciled (needs a key)
```
Green Doctor + a reconciled Azure smoke = the deployment counts correctly.

## 6. Operate

- **Status (whole stack):** `powershell -File scripts\tt-deploy.ps1 -Mode Status` (thorough; for a
  quick check use `tt-doctor.cmd` alone).
- **Watch tokens live:** `scripts\tt-live-dashboard.cmd` → http://127.0.0.1:8790
- **Backup / restore:** backups run on a schedule (verified, archive-inclusive) into
  `TRACKER_BACKUP_DIR`; restore = gunzip a `ledger-*.jsonl.gz` back to the store path. Drill:
  `scripts\recovery_drill.py`. **A backup MUST include the `.archive` segments** — the active file
  is usually empty after rotation; `backup_ledger.py` handles this, a manual copy does not.
- **Upgrade:** `git pull`, `pip install -e .`, restart the Collector (a serialization change
  requires a Collector restart — the Doctor flags runtime code/disk skew), re-run `tt-doctor`.
- **Rollback:** `git checkout <previous tag>`; the ledger is forward/backward compatible
  (schema_version is validated; unknown versions are rejected loudly, never misread).
- **Uninstall tasks:** `powershell -File scripts\tt-deploy.ps1 -Mode Uninstall` (leaves the ledger
  and auth token in place).

## 7. Connect an application

One line, no proxy required — see [ONBOARDING.md](ONBOARDING.md) and, for Azure specifically,
[AZURE_TOMORROW.md](AZURE_TOMORROW.md). Set `service_name` on every call or usage lands under
`unknown`.

## 8. Security posture

- Collector binds loopback; non-loopback binds **require** the bearer. The token lives in an
  ACL-restricted file **outside the repo**; task definitions reference the path, never the value.
- **No prompt/response text and no credentials are ever stored** — only token facts, provider ids,
  timing, and a prompt fingerprint. The Doctor runs a project-root secret scan.
- Pricing/cost is presentation-only and never written to the ledger.

## 9. Trust: proven vs assumed

Before claiming a provider is "operational", check its row in
[OPERATIONAL_EVIDENCE.md](OPERATIONAL_EVIDENCE.md). Azure OpenAI: verified (real captures, cache +
streaming). Bedrock/Vertex: run the smoke with real credentials first. The tracker never silently
guesses — a wrong or unverified count is always flagged, never a confident zero.
