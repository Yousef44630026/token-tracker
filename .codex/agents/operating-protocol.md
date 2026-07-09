# Codex Agent Operating Protocol

Use this protocol whenever a change is non-trivial, touches accounting, or may affect
operational trust.

## 1. Route

Pick the primary agent by invariant ownership, not by filename alone. A file can belong to
one folder while the risk belongs to another layer. Example: an export change that starts
summing provider totals is a core-accounting risk as much as an analytics risk.

## 2. Inspect

Before editing:

- run `git status --short`
- read the local code around the change
- check related tests
- check whether Claude or another session created new local files
- identify the invariant that could be broken

## 3. Falsify

Ask "how would this tracker lie?"

Common lies:

- double-counting subtotals
- treating unknown as exact zero
- counting a non-authoritative event
- losing a rejected event silently
- storing a derived value as source of truth
- reporting a precise total when only a lower bound is known
- confusing deployment name with model name
- merging two traces after context loss

For behavior changes, add or run the smallest test that could catch the lie.

## 4. Patch

Make the smallest durable fix that preserves the local style. Avoid unrelated refactors.
Do not hide uncertainty. If data is missing, preserve that fact as a flag, count, dead-letter,
or lower-bound signal.

## 5. Verify

Run:

- the smallest target test for the changed layer
- Ruff when Python changed
- `scripts\tt-check.cmd` when the change affects release confidence
- `scripts\tt-doctor.cmd --skip-store` for ops/security changes

Never run Black.

## 6. Report

Use this compact report:

```text
Verdict:
Changed:
Invariant risks checked:
Tests:
Remaining risk:
Next hardening step:
```

Do not say something is production-ready if live credentials, CI, scale, or security hygiene
are still unproven.

