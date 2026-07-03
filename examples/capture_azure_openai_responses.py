"""Capture a REAL Azure OpenAI Responses-API payload (the new unified /openai/v1 endpoint,
via the official `openai` Python SDK) and validate the Responses adapter against it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_azure_openai_responses.py

Unlike capture_azure_openai.py (raw REST call to the older /chat/completions route), this uses
the `openai` SDK's client.responses.create(...) against Azure's new unified endpoint shape
(base_url ending in /openai/v1) — this is the exact call shape Azure AI Foundry's own
quickstart code gives you. Provider SDKs are allowed for capturing test fixtures (project rule).

Setup:
  1. openai SDK must be installed: & python.exe -m pip install openai
  2. In Azure AI Foundry -> your resource -> deploy a Responses-capable model (e.g. gpt-5-mini,
     gpt-4o-mini) and note the DEPLOYMENT NAME.
  3. Set these in the SAME terminal you will run this script from (PowerShell):
       $env:AZURE_OPENAI_RESPONSES_ENDPOINT = "https://your-resource.services.ai.azure.com/openai/v1"
       $env:AZURE_OPENAI_RESPONSES_DEPLOYMENT = "gpt-5-mini"
       $env:AZURE_OPENAI_API_KEY = "your-api-key"
  4. Re-run this script.

Missing env vars or missing SDK -> prints instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "azure_openai_responses.REAL.json")
sys.path.insert(0, ROOT)

ENDPOINT = os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT")
DEPLOYMENT = os.environ.get("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")


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
        ("AZURE_OPENAI_RESPONSES_ENDPOINT", ENDPOINT),
        ("AZURE_OPENAI_RESPONSES_DEPLOYMENT", DEPLOYMENT),
        ("AZURE_OPENAI_API_KEY", API_KEY),
    ]
    if not val
]
if missing:
    skip(
        "[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).\n"
        "       Voir les etapes de configuration en tete de ce fichier (docstring).\n"
        "       Puis relance ce script dans le MEME terminal une fois les $env: definies."
    )

client = OpenAI(base_url=ENDPOINT, api_key=API_KEY)

print(f"--> Appel REEL Azure OpenAI Responses API (deployment={DEPLOYMENT}) ...")
try:
    response = client.responses.create(
        model=DEPLOYMENT,
        input="Reponds en un seul mot: bonjour",
    )
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    print("       (cle/endpoint invalide ? nom de deploiement incorrect ? deploiement pas encore actif ?)")
    sys.exit(1)

payload = json.loads(response.model_dump_json())

os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_deployment": DEPLOYMENT,
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> usage reel :", json.dumps(payload.get("usage", {}), ensure_ascii=False))
print("--> model (reel) :", payload.get("model"))

from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

adapter = AzureOpenAIResponsesAdapter(deployment=DEPLOYMENT)
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
    print("\n[OK] RECONCILIE sur du reel — l'adaptateur Azure OpenAI Responses TIENT sur le nouvel endpoint /openai/v1.")
elif event.event_total_mismatch is not None:
    print(f"\n[!!] MISMATCH de {event.event_total_mismatch} — le format reel differe de l'hypothese.")
    print("     C'est une VRAIE decouverte : il faut ajuster l'adaptateur au format reel.")
else:
    print("\n[i] Pas de total fourni sur ce payload — rien a reconcilier (mais l'extraction a fonctionne).")

sys.exit(0)
