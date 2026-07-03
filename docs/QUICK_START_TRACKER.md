# Quick start tracker commands

Run these from the project folder:

```powershell
cd "C:\Users\yerabhaoui\OneDrive - Deloitte (O365D)\Bureau\tracker"
```

## Count the prompt you are writing

```powershell
.\scripts\tt-count.cmd "measure this prompt before I send it" --budget-tokens 50000
```

Interactive prompt counter:

```powershell
.\scripts\tt-live.cmd
```

Type one prompt per line. It prints the TokenTap-style `cl100k_base` estimate and a
cumulative bar. This does not call Codex/Claude and does not consume credits.

## Track one real Codex prompt

```powershell
.\scripts\tt-codex-smoke.cmd
```

This runs only the first prompt in `CODEX_VARIED_TESTS.md`, imports Codex local
`token_count` usage, and shows the live bar. It consumes one real Codex call.

## Use interactive Codex with the live bar

```powershell
.\scripts\tt-codex-interactive.cmd
```

Codex opens normally. Type your prompts inside Codex. The tracker watches local Codex
`token_count` events every 2 seconds and prints the usage bar after each detected model
call. Exit Codex to see the final report.

## Track the full Codex suite

```powershell
.\scripts\tt-codex-suite.cmd
```

Useful optional arguments:

```powershell
.\scripts\tt-codex-suite.cmd --dry-run
.\scripts\tt-codex-suite.cmd --resume-complete
.\scripts\tt-codex-suite.cmd --limit 2
```

## Report and privacy audit

```powershell
.\scripts\tt-report.cmd codex_live.jsonl
.\scripts\tt-privacy.cmd codex_live.jsonl CODEX_VARIED_TESTS.md
```

The raw prompt counter estimates only the prompt text you type. Exact consumed usage after
a real Codex call comes from Codex local `token_count` events and can include hidden/system
context, files, tools, and cache.
