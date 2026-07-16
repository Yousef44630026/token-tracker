"""Capture a REAL AWS Bedrock Converse payload and validate the adapter on it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_bedrock_converse.py

Mirrors capture_gemini.py / capture_azure_openai.py, but for AWS Bedrock's Converse API. This
is a BILLED call (a tiny test call costs a fraction of a cent) — review the env vars below
before running. Uses boto3 (the official AWS SDK) for request signing (SigV4) — hand-rolling
AWS request signing would be needlessly risky; CLAUDE.md explicitly allows provider SDKs for
capturing test fixtures.

Setup (AWS Console, ~10-15 min the first time — see the accompanying guide for details).
Two auth methods are supported — use WHICHEVER you actually have:

  A) Bedrock API key (a single short- or long-term token generated directly from the
     Bedrock console's Model page) — the simpler, newer method:
       $env:AWS_BEARER_TOKEN_BEDROCK = "your-bedrock-api-key"
       $env:AWS_REGION = "us-east-1"
       $env:BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
     Note: a SHORT-term key typically expires in a matter of hours — if the call fails with
     an auth/expired error, generate a fresh one from the Bedrock console and re-set it.

  B) Classic IAM access key pair (Access Key ID + Secret Access Key from an IAM user):
       $env:AWS_ACCESS_KEY_ID = "your-access-key-id"
       $env:AWS_SECRET_ACCESS_KEY = "your-secret-access-key"
       $env:AWS_REGION = "us-east-1"
       $env:BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"

Either way: install the optional Bedrock capture extra first:
    & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install -e ".[bedrock]"
Then re-run this script in the SAME terminal where you set the variables above.

Missing boto3 or missing env vars -> prints instructions and exits cleanly (no call, no cost).
For cache accounting ground truth, prefer scripts\\tt-bedrock-cache-smoke.cmd: it performs
the required write/read pair and never stores prompt or generated content.
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "bedrock_converse.REAL.json")
sys.path.insert(0, ROOT)

BEARER_TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-micro-v1:0")


def skip(message):
    print(message)
    sys.exit(0)


try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    skip(
        "[SKIP] boto3 non installe - aucun appel effectue (cout nul).\n"
        "       Installe-le toi-meme : "
        '& "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install -e ".[bedrock]"\n'
        "       Puis relance ce script."
    )

using_bearer = BEARER_TOKEN is not None
if not REGION:
    skip(
        "[SKIP] variable manquante: AWS_REGION - aucun appel effectue (cout nul).\n"
        "       $env:AWS_REGION = 'us-east-1'  (ou la region ou tu as autorise le modele)"
    )
if not using_bearer and not (ACCESS_KEY and SECRET_KEY):
    skip(
        "[SKIP] aucune methode d'authentification complete trouvee - aucun appel effectue (cout nul).\n"
        "       Utilise SOIT AWS_BEARER_TOKEN_BEDROCK (cle Bedrock)\n"
        "       SOIT AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (paire IAM classique).\n"
        "       Voir les etapes de configuration en tete de ce fichier (docstring)."
    )


def _json_safe(value):
    """Best-effort conversion of a boto3 response into plain JSON-serializable data."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)  # fallback: never crash serialization on an unexpected type


auth_mode = "Bedrock API key (bearer token)" if using_bearer else "IAM access key pair"
print(f"--> Appel REEL AWS Bedrock Converse (model={MODEL_ID}, region={REGION}, auth={auth_mode}) ...")
try:
    if using_bearer:
        # botocore reads AWS_BEARER_TOKEN_BEDROCK from the environment automatically for
        # this service when no explicit credentials are passed — do not pass
        # aws_access_key_id/aws_secret_access_key here, or boto3 will try (and fail) to use
        # them instead of the bearer token.
        client = boto3.client("bedrock-runtime", region_name=REGION)
    else:
        client = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
        )
    response = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": "Reponds en un seul mot: bonjour"}]}],
        inferenceConfig={"maxTokens": 5},
    )
except (BotoCoreError, ClientError) as exc:
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    if using_bearer:
        print("       (cle Bedrock expiree ? les cles 'short-term' durent quelques heures -> genere-en une nouvelle)")
    print("       (cle/region invalide ? modele pas encore autorise dans 'Model access' ? quota ?)")
    sys.exit(1)

payload = _json_safe(response)

os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_model_id": MODEL_ID,
            "_region": REGION,
            "response": payload,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")
print("--> usage reel :", json.dumps(payload.get("usage", {}), ensure_ascii=False))

from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

event = normalize(payload, BedrockConverseAdapter(), context=new_trace())


def qty(token_type):
    quantity = next((x for x in event.quantities if x.token_type == token_type), None)
    return quantity.quantity if quantity else None


print("\n--- Verdict sur du REEL ---")
print(
    f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}  "
    f"cached={qty(TokenType.CACHED_INPUT)}  cache_write={qty(TokenType.CACHE_CREATION_INPUT)}"
)
print(f"  provider_total={event.provider_total_tokens}  contributing={event.event_contributing_tokens}")
print(f"  flags={event.data_quality_flags or '-'}")

if event.event_total_mismatch == 0:
    print("\n[OK] RECONCILIE sur du reel — l'adaptateur Bedrock Converse TIENT sur ce modele.")
    print("     Cet adaptateur passe de 'simule' a 'verite terrain'. A montrer en soutenance.")
elif event.event_total_mismatch is not None:
    print(f"\n[!!] MISMATCH de {event.event_total_mismatch} — le format reel differe de l'hypothese.")
    print("     C'est une VRAIE decouverte : il faut ajuster l'adaptateur au format reel.")
else:
    print("\n[i] Pas de total fourni sur ce payload — rien a reconcilier (mais l'extraction a fonctionne).")

if qty(TokenType.CACHED_INPUT) is None and qty(TokenType.CACHE_CREATION_INPUT) is None:
    print("\n[i] Ce payload ne contient pas de preuve de cache.")
    print("    Lance scripts\\tt-bedrock-cache-smoke.cmd --require-live pour une preuve write/read redacted.")

sys.exit(0)
