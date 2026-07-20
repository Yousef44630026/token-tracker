"""Capture and validate one REAL AWS Bedrock InvokeModel usage payload.

InvokeModel response bodies are model-specific. The tracker extracts exact usage only from
documented Titan Text, Nova, and Anthropic Messages body fields. AWS does not document
universal InvokeModel token-count response headers.

Run from the repository root after configuring AWS credentials, ``AWS_REGION``, and
``BEDROCK_MODEL_ID``. The script never installs dependencies and writes usage-only evidence:
prompts, generated text, embedding vectors, and credentials are excluded.
"""

import datetime
import json
import os
import sys
from collections.abc import Mapping

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
    skip("[SKIP] boto3 is not installed; no provider call was made.")

using_bearer = BEARER_TOKEN is not None
if not REGION:
    skip("[SKIP] AWS_REGION is missing; no provider call was made.")
if not using_bearer and not (ACCESS_KEY and SECRET_KEY):
    skip("[SKIP] configure AWS_BEARER_TOKEN_BEDROCK or the IAM access-key pair.")


def build_request_body(model_id: str) -> dict:
    """Construct a small request for common InvokeModel families."""
    lowered = model_id.lower()
    if "anthropic" in lowered or "claude" in lowered:
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 5,
            "messages": [{"role": "user", "content": "Reply with one word: hello"}],
        }
    if "nova" in lowered:
        return {
            "schemaVersion": "messages-v1",
            "messages": [{"role": "user", "content": [{"text": "Reply with one word: hello"}]}],
            "inferenceConfig": {"maxTokens": 5},
        }
    if "titan" in lowered and "embed" not in lowered:
        return {
            "inputText": "Reply with one word: hello",
            "textGenerationConfig": {"maxTokenCount": 5},
        }
    if "llama" in lowered:
        return {"prompt": "Reply with one word: hello", "max_gen_len": 5}
    if "mistral" in lowered:
        return {"prompt": "<s>[INST] Reply with one word: hello [/INST]", "max_tokens": 5}
    skip(f"[SKIP] no safe sample request is defined for model family: {model_id}")


def usage_only(value, depth=0):
    """Retain token-shaped fields and their container paths, never generated content."""
    if depth >= 8:
        return None
    if isinstance(value, Mapping):
        output = {}
        for key, child in list(value.items())[:128]:
            key_text = str(key)
            if "token" in key_text.lower():
                if isinstance(child, (str, int, float, bool)) or child is None:
                    output[key_text] = child
                else:
                    nested = usage_only(child, depth + 1)
                    if nested not in (None, {}, []):
                        output[key_text] = nested
                continue
            nested = usage_only(child, depth + 1)
            if nested not in (None, {}, []):
                output[key_text] = nested
        return output
    if isinstance(value, list):
        items = [usage_only(child, depth + 1) for child in value[:32]]
        return [child for child in items if child not in (None, {}, [])]
    return None


auth_mode = "Bedrock API key" if using_bearer else "IAM access key pair"
print(f"Calling REAL Bedrock InvokeModel: model={MODEL_ID}, region={REGION}, auth={auth_mode}")
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
    print(f"[FAIL] provider call failed: {type(exc).__name__}: {exc}")
    sys.exit(1)

raw_body = response.get("body")
try:
    decoded_body = json.loads(raw_body.read()) if raw_body is not None else None
except (AttributeError, TypeError, ValueError) as exc:
    print(f"[FAIL] response body could not be decoded: {type(exc).__name__}: {exc}")
    sys.exit(1)

captured_response = {
    "modelId": MODEL_ID,
    "contentType": response.get("contentType"),
    "body_json": usage_only(decoded_body) or {},
}
headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
token_headers = {str(key): value for key, value in headers.items() if "token" in str(key).lower()}

artifact = {
    "_SIMULATED": False,
    "_captured_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    "_model_id": MODEL_ID,
    "_region": REGION,
    "_privacy": "usage fields only; prompt, generated content, vectors, and credentials excluded",
    "_non_contractual_token_headers_observed": sorted(token_headers),
    "response": captured_response,
}
os.makedirs(os.path.dirname(REAL_FIXTURE), exist_ok=True)
with open(REAL_FIXTURE, "w", encoding="utf-8") as handle:
    json.dump(artifact, handle, ensure_ascii=False, indent=2)
print(f"Usage-only evidence written: {REAL_FIXTURE}")

from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

event = normalize(
    captured_response,
    BedrockInvokeModelAdapter(model_id=MODEL_ID),
    context=new_trace(),
)
print(f"contributing={event.event_contributing_tokens}")
print(f"provider_total={event.provider_total_tokens}")
print(f"flags={event.data_quality_flags or '-'}")

blocking = {
    "normalization_error",
    "provider_response_unparseable",
    "provider_usage_missing",
    "provider_usage_unverified",
    "raw_usage_missing",
    "unverified_additivity",
}
if event.event_contributing_tokens <= 0 or blocking.intersection(event.data_quality_flags):
    print("[FAIL] exact InvokeModel accounting was not demonstrated for this model response.")
    sys.exit(1)

print("[PASS] documented model-body token fields were normalized exactly.")
sys.exit(0)
