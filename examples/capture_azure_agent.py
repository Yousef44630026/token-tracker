"""EXPLORATORY capture — call a real Azure AI Foundry Agent (agents/threads/runs API) and
SEARCH the real response for token/usage information. Mirrors capture_bedrock_agent.py's
approach: we do NOT know for certain where/how token counts appear on a Run object for this
account/agent, so this script finds out from the real payload rather than assuming a shape.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\capture_azure_agent.py

Setup (once you have a test Agent created in Azure AI Foundry -> Agents -> New agent):
  1. Install the SDKs yourself if not already:
       & python.exe -m pip install azure-ai-projects azure-identity
  2. Authenticate once in this environment (interactive login, one time):
       & python.exe -m pip install azure-cli   (if you don't have the `az` CLI yet)
       az login
  3. Set (same terminal you will run this script from):
       $env:AZURE_AI_PROJECT_ENDPOINT = "https://your-project.services.ai.azure.com/api/projects/your-project-name"
       $env:AZURE_AI_AGENT_ID = "asst_xxxxxxxxxxxx"           # from the agent's page
       $env:AZURE_AI_AGENT_MODEL = "gpt-5-mini"               # your validated deployment
  4. Re-run this script.

Missing SDK, missing azure-identity auth, or missing env vars -> prints instructions and
exits cleanly (no call, no cost). This is reconnaissance, not a finished adapter: once we see
the real Run/usage shape, we build a proper adapter + ground-truth test from it.
"""

import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CAPTURE_PATH = os.path.join(ROOT, "tests", "fixtures", "realistic", "azure_agent_raw_capture.EXPLORATORY.json")
sys.path.insert(0, ROOT)

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_PROJECT_ENDPOINT")
AGENT_ID = os.environ.get("AZURE_AI_AGENT_ID")
MODEL = os.environ.get("AZURE_AI_AGENT_MODEL", "gpt-5-mini")


def skip(message):
    print(message)
    sys.exit(0)


try:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
except ImportError:
    skip(
        "[SKIP] SDK manquant - aucun appel effectue (cout nul).\n"
        '       & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" -m pip install azure-ai-projects azure-identity'
    )

missing = [
    name
    for name, val in [
        ("AZURE_AI_PROJECT_ENDPOINT", PROJECT_ENDPOINT),
        ("AZURE_AI_AGENT_ID", AGENT_ID),
    ]
    if not val
]
if missing:
    skip("[SKIP] variable(s) manquante(s): " + ", ".join(missing) + " - aucun appel effectue (cout nul).")


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if hasattr(value, "as_dict"):
        try:
            return _json_safe(value.as_dict())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def find_token_like_fields(obj, path=""):
    hits = []
    keywords = ("token", "usage")
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


print("--> Authentification (DefaultAzureCredential — az login requis au prealable) ...")
try:
    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] authentification/connexion impossible : {type(exc).__name__}: {exc}")
    print("       As-tu bien fait 'az login' dans CE terminal ? Le project endpoint est-il correct ?")
    sys.exit(1)

captured = {
    "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "_agent_id": AGENT_ID,
    "_project_endpoint": PROJECT_ENDPOINT,
}

print(f"--> Creation d'un thread + envoi d'un message REEL a l'agent {AGENT_ID} ...")
try:
    with project_client:
        agents_client = project_client.agents
        thread = agents_client.threads.create()
        captured["thread"] = _json_safe(thread)
        agents_client.messages.create(thread_id=thread.id, role="user", content="Reponds en une courte phrase: bonjour")

        run = agents_client.runs.create(thread_id=thread.id, agent_id=AGENT_ID)
        print(f"    run cree ({run.id}), attente de la fin (polling) ...")
        for _ in range(60):
            run = agents_client.runs.get(thread_id=thread.id, run_id=run.id)
            if run.status in ("completed", "failed", "cancelled", "expired"):
                break
            time.sleep(1)
        captured["run"] = _json_safe(run)

        messages = list(agents_client.messages.list(thread_id=thread.id))
        captured["messages"] = _json_safe(messages)
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] appel impossible : {type(exc).__name__}: {exc}")
    print("       (methode SDK differente de celle attendue ? agent_id invalide ? endpoint incorrect ?)")
    print("       Colle-moi ce traceback complet — les noms de methode de ce SDK peuvent avoir change.")
    captured["_error"] = f"{type(exc).__name__}: {exc}"
    os.makedirs(os.path.dirname(CAPTURE_PATH), exist_ok=True)
    with open(CAPTURE_PATH, "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2, default=str)
    sys.exit(1)

os.makedirs(os.path.dirname(CAPTURE_PATH), exist_ok=True)
with open(CAPTURE_PATH, "w", encoding="utf-8") as f:
    json.dump(captured, f, ensure_ascii=False, indent=2, default=str)
print(f"--> Capture brute sauvegardee : {CAPTURE_PATH}")

hits = find_token_like_fields(captured)
print("\n--- Verdict : cherche-t-on des tokens dans cette capture ? ---")
if hits:
    print(f"[OK] {len(hits)} champ(s) evoquant des tokens/usage trouve(s) :")
    for path, value in hits:
        print(f"    {path} = {value!r}")
    print("\n     -> Il y a de la matiere pour construire un adaptateur Azure Agent.")
    print("        Colle-moi cette sortie pour qu'on le construise.")
else:
    print("[i] AUCUN champ evoquant token/usage trouve dans cette capture.")
    print("    Colle-moi quand meme la sortie complete (et le fichier .EXPLORATORY.json) pour verifier ensemble.")

sys.exit(0)
