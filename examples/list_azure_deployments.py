"""List your Azure OpenAI DEPLOYMENTS (not base catalog models) — ZERO token cost.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\list_azure_deployments.py

The key test lists the base model CATALOG. The matrix runner needs a DEPLOYMENT name (the name
you chose in the portal). This queries the data-plane deployments endpoint and prints, for each
deployment: its name, the base model behind it, and whether it supports the cases in Family A.

Environment (same terminal):
    $env:AZURE_OPENAI_API_KEY = "..."
    $env:AZURE_OPENAI_ENDPOINT = "https://your-resource.openai.azure.com"
      (AZURE_OPENAI_RESPONSES_ENDPOINT also accepted; a trailing /openai/v1 is stripped)
Also read from a repo-root .env.
"""

import json
import os
import sys
from urllib import error as urlerr
from urllib import request as urlreq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv() -> None:
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
RAW_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

if not (API_KEY and RAW_ENDPOINT):
    print("[SKIP] AZURE_OPENAI_API_KEY and an endpoint are required. Nothing called (zero cost).")
    sys.exit(0)

# The deployments list is a classic data-plane call on the resource base (no /openai/v1 suffix).
BASE = RAW_ENDPOINT.rstrip("/")
for suffix in ("/openai/v1", "/openai"):
    if BASE.endswith(suffix):
        BASE = BASE[: -len(suffix)]

# Older api-versions still expose the data-plane deployments list; newer ones (and some Foundry
# resources) 404 it. Try a few, both auth styles, before falling back to the v1 catalog.
_DEPLOYMENT_API_VERSIONS = [API_VERSION, "2023-05-15", "2023-03-15-preview", "2022-12-01"]


def fetch(url: str, auth_style: str):
    header = {"Authorization": f"Bearer {API_KEY}"} if auth_style == "bearer" else {"api-key": API_KEY}
    req = urlreq.Request(url, method="GET", headers=header)
    try:
        with urlreq.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urlerr.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


deployments: list = []
found_via = ""
for version in _DEPLOYMENT_API_VERSIONS:
    url = f"{BASE}/openai/deployments?api-version={version}"
    for auth in ("api-key", "bearer"):
        status, body = fetch(url, auth)
        if status == 200:
            try:
                deployments = json.loads(body).get("data", [])
            except (json.JSONDecodeError, AttributeError):
                deployments = []
            found_via = f"deployments (api-version={version}, auth={auth})"
            break
    if deployments:
        break

# Fallback: the deployments endpoint is not exposed here — show the v1 catalog we KNOW works,
# labeled honestly as addressable model names (usable as `model` on the /openai/v1 surface).
catalog_fallback = not deployments
if catalog_fallback:
    status, body = fetch(f"{BASE}/openai/v1/models", "bearer")
    if status != 200:
        print(f"\n[FAIL] ni /openai/deployments ni /openai/v1/models exploitables (dernier HTTP {status}).")
        print(f"       {(body or '')[:200]}")
        print("       Lis tes déploiements dans le portail Azure AI Foundry -> section Deployments.")
        sys.exit(1)
    try:
        deployments = json.loads(body).get("data", [])
    except (json.JSONDecodeError, AttributeError):
        deployments = []
    found_via = "catalogue /openai/v1/models (noms de modèles adressables, PAS des déploiements)"

if not deployments:
    print("\n[i] Aucun déploiement ni modèle trouvé. Crée un déploiement dans le portail Foundry (Deployments).")
    sys.exit(0)

print(f"--> Source : {found_via}")


def base_model(dep: dict) -> str:
    model = dep.get("model")
    if isinstance(model, dict):  # some api-versions nest it
        return str(model.get("name") or model.get("id") or "?")
    return str(model or "?")


def supports(model: str) -> str:
    m = model.lower()
    tags = []
    if any(x in m for x in ("gpt-4o", "gpt-4.1", "o1", "o3", "o4")):
        tags.append("cache(A2/A3)")
    if any(x in m for x in ("o1", "o3", "o4")):
        tags.append("reasoning(A4/A5)")
    if "embedding" in m:
        tags.append("embeddings(A6)")
    if any(x in m for x in ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "vision")):
        tags.append("vision(A7)")
    return ", ".join(tags) if tags else "chat de base (A1, A9)"


col = "MODÈLE ADRESSABLE" if catalog_fallback else "DEPLOYMENT (à utiliser)"
print(f"\n--- {len(deployments)} entrée(s) ---")
print(f"  {col:<34} {'modèle de base':<26} cas Famille A")
print("  " + "-" * 90)
for dep in deployments:
    name = str(dep.get("id") or dep.get("name") or "?")
    model = name if catalog_fallback else base_model(dep)
    print(f"  {name:<34} {model:<26} {supports(model)}")

print("\n--- Quoi mettre dans l'environnement du runner ---")
if catalog_fallback:
    print("  [!] Aucun DÉPLOIEMENT listable : cette ressource expose la surface /openai/v1 (on")
    print("      adresse les modèles par NOM), pas les déploiements classiques. Deux conséquences :")
    print("      - le runner actuel (chemin /openai/deployments/<nom>) pourrait renvoyer 404 ici ;")
    print("      - dis-le-moi et j'ajoute une variante v1 du runner (POST /openai/v1/chat/completions).")
    print("      Vérifie aussi tes VRAIS déploiements dans le portail Foundry -> Deployments.")
else:
    print(f"  set AZURE_OPENAI_ENDPOINT={BASE}")
    print("  set AZURE_OPENAI_DEPLOYMENT=<un déploiement chat ci-dessus, idéalement gpt-4o/gpt-4.1>")
    print("  set AZURE_OPENAI_REASONING_DEPLOYMENT=<un déploiement o-series, si tu en as>")
    print("  set AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT=<un déploiement text-embedding-3-*, si tu en as>")
sys.exit(0)
