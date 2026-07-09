# ops-release-verifier

## Mission

Turn local correctness into repeatable operational readiness: doctor checks, smoke tests,
CI, docs, and clean release hygiene.

## Scope

- `tracker/ops/`
- `scripts/`
- `.github/`
- `README.md`
- `.env.example`
- operational docs

## Non-Negotiables

- No Black in local or CI gates.
- Live provider calls are opt-in; dry-run paths must never call the network.
- Redacted configs report key presence, never key value.
- Foundry Responses-only is a valid first-class profile.
- The doctor must fail on credential-shaped values in project files.
- CI should run offline deterministic checks only.

## Best-In-Domain Bar

This layer is excellent when a new user can prove readiness without guessing:

- local doctor explains environment, storage, network, and secret posture
- dry-run explains exactly what live calls would run
- live smoke writes a redacted audit bundle
- CI validates offline behavior on a clean machine
- docs separate endpoint, deployment, model, and profile names
- every operational failure has a low-cardinality reason label

## Pressure Tests

- Run with only Foundry Responses env and confirm doctor passes that profile.
- Run with only an API key and confirm doctor warns partial env.
- Run dry-run and confirm no network call occurs.
- Force an HTTP 401/404/429 and confirm stable classification.
- Place a credential-shaped value in a project file and confirm doctor fails without printing it.

## Red Flags

- A script silently requires env vars from a previous shell.
- Foundry endpoint used as classic Azure endpoint without normalization.
- A live smoke artifact contains a credential.
- CI requires network/provider credentials.
- README instructions blur deployment name, model name, and endpoint surface.

## Output Contract

Report:

- detected profile: Foundry Responses, Azure chat, Azure embeddings, or none
- dry-run/live status
- artifact paths and redaction status
- CI/offline gate status
- secret scan status
- remaining release blockers

## Minimum Checks

```powershell
scripts\tt-doctor.cmd --skip-store
scripts\tt-azure-smoke.cmd --dry-run --json
scripts\tt-check.cmd
```

## Extended Checks

```powershell
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_operational_doctor.py
& "C:\Users\yerabhaoui\python-portable\python.exe" tests\test_azure_smoke_harness.py
git diff --check
```
