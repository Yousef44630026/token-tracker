"""Capture a REAL payload from AWS Bedrock's OpenAI-COMPATIBLE endpoint (Chat Completions) and
validate the adapter on it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_bedrock_openai_compat.py

Context: Bedrock exposes an OpenAI-compatible Chat Completions surface at
    https://bedrock-runtime.{region}.amazonaws.com/openai/v1/chat/completions
callable with the standard `openai` SDK, authenticating with a Bedrock API key as the
api_key. Claude models on this surface support /chat/completions but NOT /v1/responses (that
route is OpenAI-specific — calling it is what produced the 400 you saw).

Because this surface returns OpenAI-SHAPED usage (prompt_tokens / completion_tokens /
total_tokens), the OpenAIChatCompletionsAdapter is the correct extractor even though the model
is a Claude on Bedrock. That is itself the finding to confirm on real data: a Claude served
through an OpenAI-compatible gateway reports usage in OpenAI's format, and our OpenAI adapter
reconciles it. (The `provider` label will read "openai" = the WIRE FORMAT, not the vendor;
that is a labelling nuance, not a counting error — noted honestly below.)

Setup (reuse the SAME Bedrock API key + region you already use):
  $env:AWS_BEARER_TOKEN_BEDROCK = "your-bedrock-api-key"
  $env:AWS_REGION = "eu-west-3"
  $env:BEDROCK_OPENAI_MODEL = "anthropic.claude-haiku-4-5"    # the model name the endpoint accepts
  (optional) $env:BEDROCK_OPENAI_BASE_URL = "https://bedrock-runtime.eu-west-3.amazonaws.com/openai/v1"

Install the openai SDK yourself first (this script never installs anything):
  & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install openai

Missing SDK or missing env vars -> prints instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "bedrock_openai_compat_chat.REAL.json")
sys.path.insert(0, ROOT)

BEARER_TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
MODEL = os.environ.get("BEDROCK_OPENAI_MODEL", "anthropic.claude-haiku-4-5")
BASE_URL = os.environ.get("BEDROCK_OPENAI_BASE_URL")


def skip(message):
    print(message)
    sys.exit(0)


try:
    from openai import OpenAI
except ImportError:
    skip(
        "[SKIP] SDK openai non installe - aucun appel effectue (cout nul).\n"
        '       & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install openai'
    )

missing = [
    name
    for name, val in [
        ("AWS_BEARER_TOKEN_BEDROCK", BEARER_TOKEN),
        ("AWS_REGION", REGION),
    ]
    if not val
]
if missing:
    skip("[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).")

if not BASE_URL:
    BASE_URL = f"https://bedrock-runtime.{REGION}.amazonaws.com/openai/v1"

client = OpenAI(base_url=BASE_URL, api_key=BEARER_TOKEN)

print(f"--> Appel REEL Bedrock OpenAI-compat Chat Completions (model={MODEL}, base_url={BASE_URL}) ...")
try:
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
        max_tokens=5,
    )
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    print("       (cle Bedrock expiree ? modele non autorise ? mauvaise region/base_url ? profil d'inference requis ?)")
    sys.exit(1)

payload = json.loads(completion.model_dump_json())

os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_model": MODEL,
            "_base_url": BASE_URL,
            "_note": "Claude on Bedrock via the OpenAI-compatible chat completions surface; usage is OpenAI-shaped.",
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> usage reel :", json.dumps(payload.get("usage", {}), ensure_ascii=False))
print("--> model (reel) :", payload.get("model"))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

event = normalize(payload, OpenAIChatCompletionsAdapter(), context=new_trace())


def qty(token_type):
    quantity = next((x for x in event.quantities if x.token_type == token_type), None)
    return quantity.quantity if quantity else None


print("\n--- Verdict sur du REEL ---")
print(f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}  cached={qty(TokenType.CACHED_INPUT)}")
print(f"  provider_total={event.provider_total_tokens}  contributing={event.event_contributing_tokens}")
print(f"  flags={event.data_quality_flags or '-'}")

if event.event_total_mismatch == 0:
    print("\n[OK] RECONCILIE sur du reel — Claude via l'endpoint OpenAI-compat de Bedrock renvoie un usage")
    print("     au format OpenAI, et l'adaptateur Chat Completions le reconcilie exactement.")
    print("     (Rappel: provider='openai' = le FORMAT du fil, pas le vendeur. Nuance de label, pas d'erreur de compte.)")
elif event.event_total_mismatch is not None:
    print(f"\n[!!] MISMATCH de {event.event_total_mismatch} — le format reel differe de l'hypothese OpenAI.")
    print("     VRAIE decouverte : Bedrock ne mappe pas exactement l'usage au format OpenAI. Colle-moi la sortie.")
else:
    print("\n[i] Pas de total fourni sur ce payload — rien a reconcilier (mais l'extraction a fonctionne).")

sys.exit(0)
