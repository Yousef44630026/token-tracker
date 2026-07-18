# Codex prompt — PROVE token counting is perfect

> Paste the block below into Codex (or tell Codex: "follow CODEX_VERIFY_PROMPT.md").
> Goal: an audit that proves every provider token is correctly categorized and the counting
> logic is fully respected — verified by SIMULATED tests, with zero regressions.

---

You are a **verification engineer** auditing an AI token-tracking library. Your job is to
**PROVE the token counting is perfect**: every provider token is correctly categorized
(`token_type` + `additivity` + `precision`) and the counting logic holds on every fixture.
**Add tests and a report. Do NOT weaken the library to make a test pass** — if a test fails,
you found a real bug: fix the bug and document it.

## How to run (Windows, portable Python, no pytest)
- One test: `python tests/test_NAME.py`
  (each test prints `[PASS]`/`[FAIL]` and exits non-zero on failure).
- Full suite: loop every `tests\test_*.py` and assert 0 failures.

## Key files
- Additivity truth table: `tracker/normalization/additivity.py` (`_TABLE`, `_PROVIDER_ALIASES`).
- Single assembly point: `tracker/normalization/normalizer.py` (`normalize`).
- Derived totals (never stored): `tracker/derive/` (`quantity_in_total`, `event_contributing_tokens`, `event_total_mismatch`).
- Adapters: `tracker/adapters/`. Fixtures: `tests/fixtures/` + `tests/fixtures/realistic/` (all `_SIMULATED`).

## The counting rules that MUST hold (verify each, do not assume)
1. **Total = Σ `quantity_in_total` ONLY.** Never sum the raw `quantity` column, never sum
   `provider_total_tokens` across events.
2. **Additivity per (provider, token_type) must match REAL provider semantics:**
   - `subtotal_of` ⇒ contributes **0** (it's a slice of a parent): OpenAI `cached_input`,
     `reasoning`, `audio_*`; Gemini `cached_input`, image/audio/video modality.
   - `total_contributing` ⇒ counts: `input`, `output`; Gemini `thinking`; **Anthropic and
     Bedrock `input` + `cache_read` + `cache_creation` (separate additive buckets)**;
     `embedding`; `rerank_input`.
   - `unverified` ⇒ contributes **0 + raises `unverified_additivity`** for any unregistered
     `(provider, token_type)` (fail-closed).
3. **Reconciliation:** when a provider total exists, `event_total_mismatch == 0`. When a token
   field is renamed/dropped/added (drift), the event is **flagged** (`raw_usage_missing`,
   `provider_total_mismatch`, or `provider_schema_drift`) — **never silently trusted**.
4. **INV-1..7** hold (esp. INV-4 additivity adapter-assigned, INV-5 supersession contributes 0,
   INV-6 unknown≠zero, INV-7 non-authoritative observation contributes 0).

## Verification tasks (add as NEW tests; keep all existing tests green)
1. **`tests/test_categorization_matrix.py`** — assert EVERY entry in `_TABLE` has exactly the
   additivity + `subtotal_of` specified in rule 2; assert a built `subtotal_of` quantity has
   `quantity_in_total == 0` and a `total_contributing` one has `quantity_in_total == quantity`;
   assert a representative UNREGISTERED `(provider, token_type)` resolves to `unverified`.
2. **`tests/test_reconciliation_audit.py`** — discover EVERY `*.SIMULATED.json` / `*.REAL.json`
   under `tests/fixtures/realistic/`, map each to its adapter, run `normalize()`, and assert:
   `event_total_mismatch in (0, None)`; `Σ quantity_in_total == event_contributing_tokens`;
   every `subtotal_of` quantity contributes 0; no negative quantity; if `provider_total` is
   present, it equals `Σ quantity_in_total`. **Fail loudly if any fixture has no mapped adapter**
   (so future fixtures cannot escape the audit).
3. **`tests/test_categorization_completeness.py`** — for each provider adapter, list the token
   fields its documented usage object can contain; assert each is EITHER mapped to a
   `token_type` OR in an explicit `ALLOWED_IGNORED` set with a reason. No silent third category
   (this catches a provider field that is neither counted nor consciously ignored).
4. **`tests/test_double_count_guard.py`** — for OpenAI and Anthropic, build a full multi-quantity
   event (input + output + cache + reasoning/thinking + audio where applicable) and assert
   `event_contributing_tokens` equals exactly the additive buckets, i.e. subtotals add nothing.
5. Run the **full suite**; everything green.

## Deliverables
- **`VERIFICATION_REPORT.md`**: a table `provider | token_type | additivity | contributes? |
  verified-by (test name)`, plus a "Gaps found" section (each gap: was it a real bug? fixed?).
- Update **`CODEX_DIRECTIVES.md`** JOURNAL: what you did + final file/assertion counts.

## Hard constraints
- No real credentials, no network calls. Fixtures stay `_SIMULATED` and faithful to documented
  shapes. `*.REAL.json` files (if present) are used read-only.
- Do not change `additivity.py` semantics unless the audit proves the current value is WRONG
  vs the provider's real behavior — and then document the change + evidence in the report.
- Every claim in `VERIFICATION_REPORT.md` must be backed by a named passing test.
