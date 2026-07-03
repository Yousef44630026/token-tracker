"""Capture a REAL AWS Bedrock InvokeModel payload and validate the adapter on it.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_bedrock_invoke_model.py

This is the SIBLING of capture_bedrock_converse.py but exercises a DIFFERENT extraction path:
  - Converse returns a unified `usage` object in the response BODY.
  - InvokeModel returns a MODEL-SPECIFIC body, and the token counts live in the HTTP HEADERS
    (x-amzn-bedrock-input-token-count / x-amzn-bedrock-output-token-count) — the one place
    that is identical across Titan / Nova / Llama / Cohere on Bedrock.

So this validates that BedrockInvokeModelAdapter really reads those headers off a real boto3
response, whatever the body shape. Reuses the SAME auth as capture_bedrock_converse.py (bearer
token OR IAM pair) and the SAME region/model env vars.

Setup (same as Converse — reuse what already works):
  $env:AWS_BEARER_TOKEN_BEDROCK = "your-bedrock-api-key"    # OR the IAM pair below
  $env:AWS_REGION = "eu-west-3"
  $env:BEDROCK_MODEL_ID = "arn:aws:bedrock:eu-west-3:...:inference-profile/eu.amazon.nova-micro-v1:0"
  (IAM alternative: AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY instead of the bearer token)

Install boto3 yourself first (this script never installs anything):
  & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install boto3

Missing boto3 or missing env vars -> prints instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "realistic", "bedrock_invoke_model.REAL.json")
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
        '       & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install boto3'
    )

using_bearer = BEARER_TOKEN is not None
if not REGION:
    skip("[SKIP] variable manquante: AWS_REGION - aucun appel effectue (cout nul).")
if not using_bearer and not (ACCESS_KEY and SECRET_KEY):
    skip(
        "[SKIP] aucune methode d'authentification complete trouvee - aucun appel effectue (cout nul).\n"
        "       AWS_BEARER_TOKEN_BEDROCK OU (AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY)."
    )


def _json_safe(value):
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
    return str(value)


def build_request_body(model_id: str) -> dict:
    """Construct a model-appropriate InvokeModel body. Token counts come from headers either
    way, so the body only has to be VALID for the target model family, not uniform."""
    mid = model_id.lower()
    if "anthropic" in mid or "claude" in mid:
        # Anthropic Claude on Bedrock InvokeModel schema (Messages format).
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
        }
    if "nova" in mid:
        # Amazon Nova InvokeModel schema.
        return {
            "schemaVersion": "messages-v1",
            "messages": [{"role": "user", "content": [{"text": "Reponds en un seul mot: bonjour"}]}],
            "inferenceConfig": {"maxTokens": 5},
        }
    if "titan" in mid:
        # Amazon Titan Text InvokeModel schema.
        return {
            "inputText": "Reponds en un seul mot: bonjour",
            "textGenerationConfig": {"maxTokenCount": 5},
        }
    if "llama" in mid:
        # Meta Llama InvokeModel schema.
        return {"prompt": "Reponds en un seul mot: bonjour", "max_gen_len": 5}
    if "mistral" in mid:
        return {"prompt": "<s>[INST] Reponds en un seul mot: bonjour [/INST]", "max_tokens": 5}
    # Unknown family: default to the Nova/messages schema and let the API tell us if wrong.
    return {
        "schemaVersion": "messages-v1",
        "messages": [{"role": "user", "content": [{"text": "Reponds en un seul mot: bonjour"}]}],
        "inferenceConfig": {"maxTokens": 5},
    }


auth_mode = "Bedrock API key (bearer token)" if using_bearer else "IAM access key pair"
print(f"--> Appel REEL AWS Bedrock InvokeModel (model={MODEL_ID}, region={REGION}, auth={auth_mode}) ...")
try:
    if using_bearer:
        client = boto3.client("bedrock-runtime", region_name=REGION)
    else:
        client = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
        )
    response = client.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(build_request_body(MODEL_ID)),
        contentType="application/json",
        accept="application/json",
    )
except (BotoCoreError, ClientError) as exc:
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    if using_bearer:
        print("       (cle Bedrock expiree ? les cles 'short-term' durent quelques heures -> genere-en une nouvelle)")
    print("       (corps de requete inadapte au modele ? modele pas autorise ? profil d'inference requis ?)")
    sys.exit(1)

# The response body is a StreamingBody — read it, then keep ResponseMetadata (with the headers).
raw_body = response.get("body")
body_text = None
if raw_body is not None and hasattr(raw_body, "read"):
    try:
        body_text = raw_body.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        body_text = None

# Reassemble a plain, JSON-safe response WITHOUT the unreadable StreamingBody, but WITH the
# ResponseMetadata (this is where the token-count headers live — the whole point).
captured_response = {
    "ResponseMetadata": _json_safe(response.get("ResponseMetadata", {})),
    "body_text": body_text,
    "contentType": response.get("contentType"),
}

os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_SIMULATED": False,
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_model_id": MODEL_ID,
            "_region": REGION,
            "response": captured_response,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Vrai payload sauvegarde : {REAL_FIXTURE}")

headers = captured_response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
token_headers = {k: v for k, v in headers.items() if "token" in k.lower()}
print(f"--> en-tetes de tokens reels : {json.dumps(token_headers, ensure_ascii=False) or '(aucun)'}")

from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

event = normalize(captured_response, BedrockInvokeModelAdapter(), context=new_trace())


def qty(token_type):
    quantity = next((x for x in event.quantities if x.token_type == token_type), None)
    return quantity.quantity if quantity else None


print("\n--- Verdict sur du REEL ---")
print(f"  input={qty(TokenType.INPUT)}  output={qty(TokenType.OUTPUT)}")
print(f"  provider_total={event.provider_total_tokens}  contributing={event.event_contributing_tokens}")
print(f"  flags={event.data_quality_flags or '-'}")

if "raw_usage_missing" in event.data_quality_flags:
    print("\n[!!] Aucun en-tete de tokens trouve dans la reponse reelle.")
    print("     C'est une VRAIE decouverte : soit ce modele ne renvoie pas ces en-tetes via InvokeModel,")
    print("     soit boto3 les expose ailleurs. Colle-moi la sortie complete pour verifier ensemble.")
elif event.event_contributing_tokens and event.event_contributing_tokens > 0:
    print("\n[OK] EXTRACTION REELLE reussie — l'adaptateur Bedrock InvokeModel lit bien les en-tetes de tokens.")
    print("     Cet adaptateur passe de 'simule' a 'verite terrain' (chemin en-tetes, distinct de Converse).")
else:
    print("\n[i] Extraction sans erreur mais 0 token contributif — inspecte les en-tetes ci-dessus.")

sys.exit(0)
