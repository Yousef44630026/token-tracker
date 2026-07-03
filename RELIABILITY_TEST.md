# Claude tracker reliability test

Use a fresh file so earlier events created under older accounting rules do not mix with the
new run.

## Automated per-prompt run

This mode is best when you want a clean per-prompt report. It runs each prompt as a
separate Claude command, then groups every underlying API call under that prompt's
`suite_prompt_*` metadata. It does not preserve one interactive Claude conversation across
the prompts.

```powershell
$proxy = "C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
$claude = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe\claude.exe"
Remove-Item -LiteralPath ".\reliability_suite.jsonl" -ErrorAction SilentlyContinue
& $proxy prompt-suite `
  --provider anthropic `
  --store ".\reliability_suite.jsonl" `
  --prompts ".\RELIABILITY_TEST.md" `
  -- $claude -p "{prompt}" `
  --safe-mode `
  --no-session-persistence `
  --output-format json `
  --model sonnet
```

Use `--dry-run` before the real run if you only want to verify the prompt labels and hashes
without making provider calls.

If a long run is interrupted, rerun the same command with `--resume-complete`; completed
prompts already present in the JSONL will be skipped automatically. After any run, audit
the capture:

```powershell
& $proxy privacy-audit `
  --store ".\reliability_suite.jsonl" `
  --prompts ".\RELIABILITY_TEST.md"
```

To export the per-prompt table for Excel:

```powershell
& $proxy report `
  --store ".\reliability_suite.jsonl" `
  --per-prompt-csv ".\reliability_suite_prompts.csv"
```

For scenario suites with known expected output formats, add `--quality-checks`. Quality
checks inspect the child-process output during the run but do not persist raw answers.

To display a live token-budget bar during a run, add for example:

```powershell
--live-budget-tokens 300000
```

This shows provider-reported tracker tokens used/left against your chosen budget. It does
not read an account-level Claude Pro or ChatGPT Plus remaining-credit balance.

## Manual same-session run

Use this mode when you specifically want Claude context/cache behavior inside one
interactive session.

```powershell
$proxy = "C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
$claude = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe\claude.exe"
Remove-Item -LiteralPath ".\reliability_claude.jsonl" -ErrorAction SilentlyContinue
& $proxy run --provider anthropic --store ".\reliability_claude.jsonl" -- $claude
```

Send these prompts in the same Claude session, one at a time, and wait for each answer.

1. Minimal output:

   ```text
   Réponds uniquement par le mot OK.
   ```

2. Multilingual/tokenizer stress:

   ```text
   Sans utiliser d’outil, réponds en trois lignes : une en français, une en anglais,
   et une contenant exactement ces symboles : 🚀 café naïve JSON {"ok": true}.
   ```

3. First file read/cache creation:

   ```text
   Lis README.md et donne exactement cinq points décrivant l’architecture du projet.
   ```

4. Reuse the same context/cache:

   ```text
   En te basant sur le même README.md, cite maintenant les trois invariants les plus
   importants, sans relire d’autres fichiers.
   ```

5. Larger code/tool call:

   ```text
   Lis tracker/proxy/server.py et identifie trois risques techniques. Pour chacun, donne
   le nom de la fonction concernée et une correction courte. Ne modifie aucun fichier.
   ```

6. Multi-file reasoning:

   ```text
   Compare pyproject.toml, tracker/proxy/server.py et tracker/models/token_event.py.
   Explique en six points comment un appel Claude devient un événement JSONL.
   Ne modifie aucun fichier.
   ```

Exit Claude with `/exit`, then generate the report:

```powershell
& $proxy report --store ".\reliability_claude.jsonl"
```

Healthy results:

- no startup-only event and `incomplete events: 0`;
- every event has an exact provider usage and UTC timestamp;
- Anthropic cache read and cache creation contribute to totals without
  `unverified_additivity`;
- the JSONL contains hashes and measurements, not prompt text or credentials;
- TokenTap-style estimates may differ substantially from provider prompt tokens. This
  difference measures estimator coverage, not tracker accounting accuracy.
- new captures show `complete`/`failed` statuses, provider request/response ids when
  available, latency/TTFT, and prompt sequence/cycle without storing prompt content;
- failed/incomplete events remain auditable but contribute zero to authoritative rollups.
