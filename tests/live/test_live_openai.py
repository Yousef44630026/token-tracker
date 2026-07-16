"""LIVE — real OpenAI API call -> capture real payload -> run the adapter.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\live\\test_live_openai.py

NOT part of the default suite (it lives under tests/live/, which the suite glob skips). It
makes a REAL, billable API call, so it only runs when OPENAI_API_KEY is set; otherwise it
SKIPS cleanly (exit 0, no call, no cost). The key is read from the environment only — never
hard-coded (see .env.example).

When it runs it:
  1. makes one tiny call (default gpt-4o-mini, max_tokens small -> a fraction of a cent),
  2. saves the REAL response to tests/fixtures/openai_chat_completions.REAL.json,
  3. feeds it to OpenAIChatCompletionsAdapter and checks the tokens reconcile.

This is how a SIMULATED fixture becomes a ground-truth one.
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures")
sys.path.insert(0, PROJECT_ROOT)

API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print("[SKIP] OPENAI_API_KEY absente — aucun appel reel effectue (cout nul).")
    print("       Pour lancer: definir OPENAI_API_KEY (cle API avec credit), puis relancer ce script.")
    sys.exit(0)

MODEL = os.environ.get("LIVE_MODEL", "gpt-4o-mini")
_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


try:
    from openai import OpenAI
except Exception as exc:  # noqa: BLE001
    print(f"[SKIP] SDK openai indisponible: {exc}")
    sys.exit(0)

print(f"--> Appel reel OpenAI (modele={MODEL}) ...")
try:
    client = OpenAI()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
        max_tokens=5,
    )
except Exception as exc:  # noqa: BLE001 — auth/quota/network: report, don't crash with a traceback
    print(f"[FAIL] l'appel API a echoue: {type(exc).__name__}: {exc}")
    print("       (cle invalide ? pas de credit ? reseau ?)")
    sys.exit(1)

payload = resp.model_dump()

# 1) save the REAL payload as a ground-truth fixture
os.makedirs(FIXTURES, exist_ok=True)
real_path = os.path.join(FIXTURES, "openai_chat_completions.REAL.json")
with open(real_path, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_model": MODEL,
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde: {real_path}")

# 2) run the adapter on the REAL payload
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402

usage = OpenAIChatCompletionsAdapter().extract_usage_from_response(payload)
inp = next((q for q in usage.quantities if q.token_type == TokenType.INPUT), None)
out = next((q for q in usage.quantities if q.token_type == TokenType.OUTPUT), None)

check(inp is not None and inp.quantity > 0, f"input tokens reels > 0 (got {getattr(inp, 'quantity', None)})")
check(out is not None and out.quantity >= 0, f"output tokens reels >= 0 (got {getattr(out, 'quantity', None)})")
check(usage.provider_total_tokens is not None and usage.provider_total_tokens > 0, "provider_total_tokens present")

event = TokenEvent(
    event_id="evt-live",
    request_correlation_id="r-live",
    trace_id="t-live",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
    observation={"authoritative": True},
)
check(event.event_total_mismatch == 0, f"contributing reconcilie avec le total fournisseur (mismatch={event.event_total_mismatch})")
print(f"--> Tokens reels: input={inp.quantity}, output={out.quantity}, total fournisseur={usage.provider_total_tokens}")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
