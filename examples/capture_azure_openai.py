"""Capture a REAL Azure OpenAI payload and validate the adapter on it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_azure_openai.py

Mirrors capture_gemini.py, but for Azure OpenAI. Unlike Gemini's free tier, an Azure OpenAI
call is billed (a tiny test call with max_tokens=5 costs a fraction of a cent) — this makes
ONE real call, so review the env vars before running.

Setup (Azure Portal, ~10 min the first time):
  1. Create an "Azure OpenAI" resource (portal.azure.com -> Create a resource -> Azure OpenAI).
  2. Open the resource -> "Go to Azure AI Foundry portal" (or "Model deployments") and deploy a
     cheap chat model (e.g. gpt-4o-mini) under a DEPLOYMENT NAME you choose.
  3. Back in the Azure OpenAI resource -> "Keys and Endpoint": copy KEY 1 and the Endpoint.
  4. Set these in the SAME terminal you will run this script from (PowerShell):
       $env:AZURE_OPENAI_API_KEY = "your-key"
       $env:AZURE_OPENAI_ENDPOINT = "https://your-resource-name.openai.azure.com"
       $env:AZURE_OPENAI_DEPLOYMENT = "your-deployment-name"
  5. Re-run this script.

No SDK needed — REST call with the standard library only. Missing env vars -> prints
instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys
from urllib import error as urlerr
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "azure_chat_completions.REAL.json")
sys.path.insert(0, ROOT)

API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")


def skip(message):
    print(message)
    sys.exit(0)


missing = [
    name
    for name, val in [
        ("AZURE_OPENAI_API_KEY", API_KEY),
        ("AZURE_OPENAI_ENDPOINT", ENDPOINT),
        ("AZURE_OPENAI_DEPLOYMENT", DEPLOYMENT),
    ]
    if not val
]
if missing:
    skip(
        "[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).\n"
        "       Voir les etapes de configuration en tete de ce fichier (docstring).\n"
        "       Puis relance ce script dans le MEME terminal une fois les $env: definies."
    )

url = f"{ENDPOINT.rstrip('/')}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version={API_VERSION}"
body = json.dumps(
    {
        "messages": [{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
        "max_tokens": 5,
    }
).encode("utf-8")
req = urlreq.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "api-key": API_KEY})

print(f"--> Appel REEL Azure OpenAI (deployment={DEPLOYMENT}, api-version={API_VERSION}) ...")
try:
    with urlreq.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read())
except urlerr.HTTPError as exc:
    detail = exc.read().decode("utf-8", "replace")[:500]
    print(f"[FAIL] HTTP {exc.code} : {detail}")
    print("       (cle/endpoint invalide ? nom de deployment incorrect ? deploiement pas encore actif ?)")
    sys.exit(1)
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    sys.exit(1)

os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_deployment": DEPLOYMENT,
            "_api_version": API_VERSION,
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> usage reel :", json.dumps(payload.get("usage", {}), ensure_ascii=False))
print("--> model (reel, pas le nom de deployment) :", payload.get("model"))

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

adapter = AzureOpenAIChatCompletionsAdapter(deployment=DEPLOYMENT)
event = normalize(payload, adapter, context=new_trace())


def qty(token_type):
    q = next((x for x in event.quantities if x.token_type == token_type), None)
    return q.quantity if q else None


print("\n--- Verdict sur du REEL ---")
print(
    f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}  "
    f"cached={qty(TokenType.CACHED_INPUT)}  reasoning={qty(TokenType.REASONING)}"
)
print(f"  provider_total={event.provider_total_tokens}  contributing={event.event_contributing_tokens}")
print(f"  flags={event.data_quality_flags or '-'}")

if event.event_total_mismatch == 0:
    print("\n[OK] RECONCILIE sur du reel — l'adaptateur Azure OpenAI (format identique a OpenAI) TIENT.")
    print("     Cet adaptateur passe de 'simule' a 'verite terrain'. A montrer en soutenance.")
elif event.event_total_mismatch is not None:
    print(f"\n[!!] MISMATCH de {event.event_total_mismatch} — le format reel differe de l'hypothese.")
    print("     C'est une VRAIE decouverte : il faut ajuster l'adaptateur au format reel.")
else:
    print("\n[i] Pas de total fourni sur ce payload — rien a reconcilier (mais l'extraction a fonctionne).")

sys.exit(0)
