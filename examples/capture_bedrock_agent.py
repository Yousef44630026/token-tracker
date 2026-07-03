"""EXPLORATORY capture — call a real "Agents for Amazon Bedrock" agent (InvokeAgent API) and
SEARCH the real response for any token/usage information. This is reconnaissance, not a
finished adapter: InvokeAgent returns an event STREAM (chunk/trace/... events), not a single
usage object like Converse, and we do not yet know for certain whether/where token counts
appear in it for your account's agent — so this script finds out from the real payload rather
than guessing.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_bedrock_agent.py

Setup (once you have a test Agent created in the Bedrock console — see the accompanying
guide):
  1. Install boto3 yourself if not already: & python.exe -m pip install boto3
  2. Set (same auth as capture_bedrock_converse.py — bearer token OR access key pair):
       $env:AWS_BEARER_TOKEN_BEDROCK = "your-bedrock-api-key"   # or AWS_ACCESS_KEY_ID/SECRET
       $env:AWS_REGION = "eu-west-3"                            # match your agent's region
       $env:BEDROCK_AGENT_ID = "your-agent-id"                  # from the Agents console page
       $env:BEDROCK_AGENT_ALIAS_ID = "your-alias-id"            # e.g. TSTALIASID for the draft test alias
  3. Re-run this script.

Missing boto3 or missing env vars -> prints instructions and exits cleanly (no call, no cost).
"""

import datetime
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CAPTURE_PATH = os.path.join(ROOT, "tests", "fixtures", "realistic", "bedrock_agent_raw_capture.EXPLORATORY.json")
sys.path.insert(0, ROOT)

BEARER_TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
AGENT_ID = os.environ.get("BEDROCK_AGENT_ID")
ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID")


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
missing = [
    name
    for name, val in [
        ("AWS_REGION", REGION),
        ("BEDROCK_AGENT_ID", AGENT_ID),
        ("BEDROCK_AGENT_ALIAS_ID", ALIAS_ID),
    ]
    if not val
]
if missing:
    skip("[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).")
if not using_bearer and not (ACCESS_KEY and SECRET_KEY):
    skip("[SKIP] aucune methode d'authentification trouvee (AWS_BEARER_TOKEN_BEDROCK ou paire IAM) - aucun appel effectue.")


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return repr(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def find_token_like_fields(obj, path=""):
    """Recursively search for any key that LOOKS token/usage-related, wherever it appears."""
    hits = []
    keywords = ("token", "usage", "inputtoken", "outputtoken")
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            if any(kw in k.lower() for kw in keywords):
                hits.append((new_path, v))
            hits.extend(find_token_like_fields(v, new_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(find_token_like_fields(v, f"{path}[{i}]"))
    return hits


auth_mode = "Bedrock API key (bearer token)" if using_bearer else "IAM access key pair"
print(f"--> Appel REEL InvokeAgent (agent={AGENT_ID}, alias={ALIAS_ID}, region={REGION}, auth={auth_mode}) ...")

try:
    if using_bearer:
        client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    else:
        client = boto3.client("bedrock-agent-runtime", region_name=REGION, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY)
    response = client.invoke_agent(
        agentId=AGENT_ID,
        agentAliasId=ALIAS_ID,
        sessionId=str(uuid.uuid4()),
        inputText="Reponds en une courte phrase: bonjour",
        enableTrace=True,  # without this, trace events (where usage might live) are omitted
    )
except (BotoCoreError, ClientError) as exc:
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    print("       (agent pas encore 'Prepared' ? alias invalide ? cle expiree ?)")
    sys.exit(1)

# --- consume the event stream: collect EVERY event, whatever its type ---
events = []
event_type_counts = {}
try:
    for event in response["completion"]:
        safe_event = _json_safe(event)
        events.append(safe_event)
        for event_type in safe_event:
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
except (BotoCoreError, ClientError) as exc:
    print(f"[FAIL] erreur en lisant le flux d'evenements : {type(exc).__name__}: {exc}")
    sys.exit(1)

print(f"--> {len(events)} evenements recus. Types : {event_type_counts}")

os.makedirs(os.path.dirname(CAPTURE_PATH), exist_ok=True)
with open(CAPTURE_PATH, "w", encoding="utf-8") as f:
    json.dump(
        {
            "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "_agent_id": AGENT_ID,
            "_alias_id": ALIAS_ID,
            "_region": REGION,
            "event_type_counts": event_type_counts,
            "events": events,
        },
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"--> Flux brut sauvegarde : {CAPTURE_PATH}")

# --- THE actual question: does anything token/usage-shaped appear anywhere in the stream? ---
hits = find_token_like_fields({"events": events})
print("\n--- Verdict : cherche-t-on des tokens dans ce flux ? ---")
if hits:
    print(f"[OK] {len(hits)} champ(s) evoquant des tokens/usage trouve(s) :")
    for path, value in hits:
        print(f"    {path} = {value!r}")
    print("\n     -> Il y a de la matiere pour construire un adaptateur InvokeAgent.")
    print("        Colle-moi cette sortie (et si besoin le fichier .EXPLORATORY.json) pour qu'on le construise.")
else:
    print("[i] AUCUN champ evoquant token/usage trouve dans ce flux d'evenements.")
    print("    -> Sur ce compte/agent, InvokeAgent ne semble pas exposer directement de comptage de")
    print("       tokens (au moins pas dans ce format simple). Colle-moi quand meme la sortie complete")
    print("       ci-dessus (event_type_counts + un evenement 'trace' si present) pour verifier ensemble.")

sys.exit(0)
