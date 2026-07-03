"""Capture a REAL Gemini payload (free Google AI Studio key) and validate the adapter on it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_gemini.py

This is the cheapest way to break the "everything is simulated" ceiling: Google AI Studio
gives a FREE API key (no card). One real call confirms — or disproves — the adapter's
assumption about Gemini's usageMetadata, turning a SIMULATED fixture into ground truth.

How to get the free key (5 min):
  1. Go to  https://aistudio.google.com/apikey  and click "Create API key".
  2. Set it:  $env:GEMINI_API_KEY = "your-key"   (PowerShell)
  3. Re-run this script.

No SDK needed — it calls the REST endpoint with the standard library only. Uses no key ->
prints these instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys
from urllib import error as urlerr
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "gemini_generate.REAL.json")
sys.path.insert(0, ROOT)

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
MODEL = os.environ.get("LIVE_GEMINI_MODEL", "gemini-2.5-flash")
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def skip(message):
    print(message)
    sys.exit(0)


if not API_KEY:
    skip(
        "[SKIP] GEMINI_API_KEY (ou GOOGLE_API_KEY) absente - aucun appel effectue (cout nul).\n"
        "       Cle GRATUITE : https://aistudio.google.com/apikey\n"
        "       Puis : $env:GEMINI_API_KEY = 'ta-cle'  et relance ce script."
    )

# --- one real, tiny generateContent call (REST, stdlib only) -----------------------------
body = json.dumps({"contents": [{"parts": [{"text": "Reponds en une courte phrase: bonjour."}]}]}).encode("utf-8")
req = urlreq.Request(
    ENDPOINT,
    data=body,
    method="POST",
    headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
)

print(f"--> Appel REEL Gemini (modele={MODEL}) ...")
try:
    with urlreq.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read())
except urlerr.HTTPError as exc:
    detail = exc.read().decode("utf-8", "replace")[:400]
    print(f"[FAIL] HTTP {exc.code} : {detail}")
    print("       (cle invalide ? modele indisponible ? quota ?)")
    sys.exit(1)
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    sys.exit(1)

# --- save the REAL payload as a ground-truth fixture (drop-in for the SIMULATED one) ------
os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
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
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> usageMetadata reel :", json.dumps(payload.get("usageMetadata", {}), ensure_ascii=False))

# --- run the adapter on the REAL payload: does the assumption hold? -----------------------
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

event = normalize(payload, GeminiGenerateContentAdapter(), context=new_trace())


def qty(token_type):
    q = next((x for x in event.quantities if x.token_type == token_type), None)
    return q.quantity if q else None


print("\n--- Verdict sur du REEL ---")
print(
    f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}  "
    f"thinking={qty(TokenType.THINKING)}  cached={qty(TokenType.CACHED_INPUT)}"
)
print(f"  provider_total={event.provider_total_tokens}  contributing={event.event_contributing_tokens}")
print(f"  flags={event.data_quality_flags or '-'}")

if event.event_total_mismatch == 0:
    print("\n[OK] RECONCILIE sur du reel — l'hypothese Gemini (total = input+output+thinking) TIENT.")
    print("     Cet adaptateur passe de 'simule' a 'verite terrain'. A montrer en soutenance.")
elif event.event_total_mismatch is not None:
    print(f"\n[!!] MISMATCH de {event.event_total_mismatch} — l'hypothese ne tient PAS exactement sur le vrai")
    print("     payload. C'est une VRAIE decouverte : il faut ajuster l'adaptateur Gemini au format reel.")
else:
    print("\n[i] Pas de total fourni sur ce payload — rien a reconcilier (mais l'extraction a fonctionne).")

sys.exit(0)
