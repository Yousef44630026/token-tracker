"""EXPERIMENTAL capture - call Cohere Command R+ via Azure AI Foundry serverless API.

This is a provider-surface validation probe, not a production-supported Azure Cohere adapter
path yet. It captures the real wire shape and runs the Cohere adapter against it so we can
decide whether Azure's serverless Cohere deployment matches the public Cohere chat API.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_azure_cohere.py

Unlike Azure OpenAI (same wire format as OpenAI), a Cohere model deployed as a Azure AI Foundry
"serverless API" is NOT OpenAI-shaped — it speaks Cohere's own chat API. Our cohere_chat_adapter
expects usage under a top-level "usage" key (matching Cohere's public API docs at the time it was
written). We do NOT assume that holds on Azure's serverless deployment surface — this script
calls the real endpoint and reports the real shape, honestly, even if it means the adapter needs
adjusting (raw_usage_missing is a correct, non-fabricated signal, not a bug).

Setup (Azure AI Foundry, ~5 min once you've deployed Command R+):
  1. In Azure AI Foundry -> Model catalog -> "Command R+" -> Deploy -> Serverless API.
  2. Deployments -> your Cohere deployment -> copy "Target URI" and "Key".
  3. Set these in the SAME terminal you will run this script from (PowerShell):
       $env:AZURE_COHERE_ENDPOINT = "https://your-deployment-name.region.models.ai.azure.com"
       $env:AZURE_COHERE_KEY = "your-key"
  4. Re-run this script.

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
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "azure_cohere_chat.REAL.json")
sys.path.insert(0, ROOT)

ENDPOINT = os.environ.get("AZURE_COHERE_ENDPOINT")
API_KEY = os.environ.get("AZURE_COHERE_KEY")


def skip(message):
    print(message)
    sys.exit(0)


missing = [
    name
    for name, val in [
        ("AZURE_COHERE_ENDPOINT", ENDPOINT),
        ("AZURE_COHERE_KEY", API_KEY),
    ]
    if not val
]
if missing:
    skip(
        "[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).\n"
        "       Voir les etapes de configuration en tete de ce fichier (docstring).\n"
        "       Puis relance ce script dans le MEME terminal une fois les $env: definies."
    )

url = f"{ENDPOINT.rstrip('/')}/v1/chat"
body = json.dumps(
    {
        "message": "Reponds en un seul mot: bonjour",
        "max_tokens": 5,
    }
).encode("utf-8")
req = urlreq.Request(
    url,
    data=body,
    method="POST",
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
)

print(f"--> Appel REEL Cohere Command R+ via Azure AI Foundry ({url}) ...")
try:
    with urlreq.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read())
except urlerr.HTTPError as exc:
    detail = exc.read().decode("utf-8", "replace")[:500]
    print(f"[FAIL] HTTP {exc.code} : {detail}")
    print("       (cle/endpoint invalide ? deploiement pas encore actif ? mauvais chemin d'API ?)")
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
            "_endpoint": ENDPOINT,
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> cles top-level du payload reel :", list(payload.keys()) if isinstance(payload, dict) else type(payload))

from tracker.adapters.cohere_chat_adapter import CohereChatAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

adapter = CohereChatAdapter()
event = normalize(payload, adapter, context=new_trace())


def qty(token_type):
    q = next((x for x in event.quantities if x.token_type == token_type), None)
    return q.quantity if q else None


print("\n--- Verdict sur du REEL ---")
print(f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}")
print(f"  contributing={event.event_contributing_tokens}  flags={event.data_quality_flags or '-'}")

if "raw_usage_missing" in event.data_quality_flags:
    print("\n[!!] L'adaptateur cherche un champ top-level 'usage' mais ne l'a pas trouve dans ce payload reel.")
    print("     C'est une VRAIE decouverte (pas un bug de capture) : Azure AI Foundry expose peut-etre")
    print("     l'usage sous une autre cle (ex. 'meta.tokens' comme l'API Cohere publique documentee).")
    print("     Colle-moi la sortie complete ci-dessus pour qu'on ajuste l'adaptateur au format reel.")
elif event.event_contributing_tokens:
    print("\n[OK] RECONCILIE sur du reel — l'adaptateur Cohere TIENT tel quel sur Azure AI Foundry.")
else:
    print("\n[i] Extraction sans erreur mais 0 token contributif — inspecte le payload complet ci-dessus.")

sys.exit(0)
