# Codex prompt tracking

This is the TokenTap-style path for Codex: launch Codex normally and import the
local `token_count` events that Codex writes after each model call.

Unlike Claude Code proxy tests, Codex logged in with ChatGPT should not be forced
through `openai_base_url`; that can break ChatGPT auth scopes. This tracker reads
Codex's local session usage instead, without storing raw prompts or credentials.

## Interactive Codex

```powershell
ai-token-tracker-proxy codex `
  --store ".\codex_events.jsonl" `
  --live-budget-tokens 50000
```

Type prompts normally in Codex. When you exit Codex, the tracker imports the new
`token_count` events and prints a report.

## One prompt

```powershell
ai-token-tracker-proxy codex `
  --store ".\codex_events.jsonl" `
  --live-budget-tokens 50000 `
  -- --no-alt-screen -a never exec --skip-git-repo-check -s read-only "Reply with exactly one word: OK"
```

## Repeatable prompt suite

```powershell
ai-token-tracker-proxy codex-suite `
  --store ".\codex_events.jsonl" `
  --prompts ".\CODEX_VARIED_TESTS.md" `
  --live-budget-tokens 150000 `
  --suppress-output
```

Use `--dry-run` first if you only want to verify the prompt labels and hashes without
making model calls. Use `--resume-complete` to continue an interrupted suite without
rerunning prompts already tracked in the same store.

By default, `codex-suite` imports only new Codex session files created during each prompt.
That avoids mixing in token events from another Codex window/session running at the same
time. Use `--include-existing-sessions` only if you intentionally want to import token
events appended to already-open Codex sessions.

## Reports and privacy audit

```powershell
ai-token-tracker-proxy report --store ".\codex_events.jsonl"
ai-token-tracker-proxy privacy-audit --store ".\codex_events.jsonl"
```

The stored JSONL contains token usage and metadata only. It does not store raw
prompt text, assistant output, or auth tokens.
`--suppress-output` also keeps child stdout/stderr from being echoed by the suite runner;
that affects terminal display only, not what the tracker stores.
