"""Test an Azure AI Foundry key — ZERO token cost.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\test_foundry_key.py

Authenticates against the Foundry endpoint by LISTING MODELS (GET .../models), which requires
a valid key but spends no tokens. Prints a clear verdict and, on failure, the most likely
cause. Standard library only — no SDK needed.

Environment (set in the SAME terminal, PowerShell). The endpoint is the Foundry /openai/v1
base; a classic Azure OpenAI endpoint also works:
    $env:AZURE_OPENAI_RESPONSES_ENDPOINT = "https://your-resource.services.ai.azure.com/openai/v1"
    $env:AZURE_OPENAI_API_KEY = "your-foundry-key"
  (falls back to $env:AZURE_OPENAI_ENDPOINT / classic .openai.azure.com if the first is unset)

Values may also live in a repo-root .env (KEY=value per line).
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
ENDPOINT = os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

if not (API_KEY and ENDPOINT):
    print(
        "[SKIP] AZURE_OPENAI_API_KEY and an endpoint "
        "(AZURE_OPENAI_RESPONSES_ENDPOINT or AZURE_OPENAI_ENDPOINT) are required.\n"
        "       Nothing was called (zero cost). See the docstring for setup."
    )
    sys.exit(0)


def models_url(endpoint: str) -> tuple[str, str]:
    """Return (url, auth_style) for a zero-cost model-listing call on this endpoint."""
    base = endpoint.rstrip("/")
    if "/openai/v1" in base:  # Foundry unified surface
        return f"{base}/models", "bearer"
    if "services.ai.azure.com" in base:  # Foundry base without the /openai/v1 suffix
        return f"{base}/openai/v1/models", "bearer"
    if "openai.azure.com" in base:  # classic Azure OpenAI
        return f"{base}/openai/models?api-version={API_VERSION}", "api-key"
    return f"{base}/models", "bearer"  # best effort for anything else


def attempt(url: str, auth_style: str):
    """Return (status, body_text). status is None on a network-level failure."""
    header = {"Authorization": f"Bearer {API_KEY}"} if auth_style == "bearer" else {"api-key": API_KEY}
    req = urlreq.Request(url, method="GET", headers=header)
    try:
        with urlreq.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urlerr.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001  (DNS / TLS / connection)
        return None, f"{type(exc).__name__}: {exc}"


url, auth = models_url(ENDPOINT)
print(f"--> Test clé Foundry (zéro token) : GET {url}  [auth={auth}]")
status, body = attempt(url, auth)

# On a 401, the other auth style sometimes succeeds (Foundry accepts both on some resources).
if status in (401, 403):
    alt = "api-key" if auth == "bearer" else "bearer"
    alt_status, alt_body = attempt(url, alt)
    if alt_status == 200:
        status, body, auth = alt_status, alt_body, alt
        print(f"    (l'en-tête '{auth}' a fonctionné là où l'autre a échoué)")


def model_ids(text: str) -> list[str]:
    try:
        data = json.loads(text).get("data", [])
        return [m.get("id", "?") for m in data if isinstance(m, dict)][:8]
    except (json.JSONDecodeError, AttributeError):
        return []


print("\n--- Verdict ---")
if status == 200:
    ids = model_ids(body)
    print(f"[OK] Clé VALIDE. L'endpoint authentifie (auth={auth}).")
    if ids:
        print(f"     Modèles/déploiements visibles : {', '.join(ids)}")
    print("     Tu peux lancer la matrice : examples\\azure_matrix_family_a.py")
    sys.exit(0)

if status in (401, 403):
    print(f"[FAIL] HTTP {status} — clé refusée ou sans permission sur cette ressource.")
    print("       Vérifie AZURE_OPENAI_API_KEY (KEY 1/2 du portail) et que la clé appartient bien à CETTE ressource.")
elif status == 404:
    print("[FAIL] HTTP 404 — endpoint/chemin introuvable.")
    print("       L'endpoint pointe-t-il bien sur la base Foundry (…/openai/v1) ou classique (…openai.azure.com) ?")
elif status is None:
    print(f"[FAIL] Connexion impossible — {body}")
    print("       Hostname d'endpoint erroné, réseau, ou proxy d'entreprise ?")
else:
    print(f"[FAIL] HTTP {status} : {body[:300]}")
sys.exit(1)
