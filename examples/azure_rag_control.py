"""RAG CONTROL — irrefutable proof that retrieval actually drives the answer (real Azure).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\azure_rag_control.py

A skeptic can object to the earlier RAG demo: "gpt-5-mini may already KNOW the Eiffel Tower —
how do you prove the retrieved context was used, not the model's own memory?"

This control settles it with a fact the model CANNOT know — a fabricated directive with an
unguessable codename and number — and a TWO-ARM comparison on real Azure:

  WITH context     -> the answer must contain the fabricated number  (retrieval was used)
  WITHOUT context  -> the answer must NOT contain it                 (the model can't know it)

If the number appears only when the context is injected, retrieval -> injection -> answer is
proven beyond doubt. Both responses are saved for the audit trail.

Same env + /openai/v1 Bearer surface as the other runners.
"""

import datetime
import json
import os
import re
import sys
import time
from urllib import error as urlerr
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "realistic")
sys.path.insert(0, ROOT)


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
RAW_ENDPOINT = os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
CHAT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")

if not (API_KEY and RAW_ENDPOINT and CHAT):
    print("[SKIP] AZURE_OPENAI_API_KEY / endpoint / AZURE_OPENAI_DEPLOYMENT required. No call made (zero cost).")
    sys.exit(0)

_BASE = RAW_ENDPOINT.rstrip("/")
V1_BASE = _BASE if _BASE.endswith("/openai/v1") else f"{_BASE.split('/openai')[0]}/openai/v1"


def post(payload: dict, *, attempts: int = 3) -> dict:
    body = json.dumps({**payload, "model": CHAT}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    req = urlreq.Request(f"{V1_BASE}/chat/completions", data=body, method="POST", headers=headers)
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlreq.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read())
        except urlerr.HTTPError:
            raise  # a real HTTP status (4xx/5xx) will not fix itself — surface it
        except (TimeoutError, urlerr.URLError) as exc:  # transient network / corporate proxy
            last = exc
            if attempt < attempts - 1:
                print(f"  [retry] tentative {attempt + 1} a échoué (réseau), nouvelle tentative...")
                time.sleep(3 * (attempt + 1))
    raise last  # type: ignore[misc]


def answer_of(response: dict) -> str:
    choices = response.get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""


# --- a fabricated fact the model cannot possibly know (unguessable codename + number) -------
FACT_TOKEN = "812"
FABRICATED = "The Vanthys Retention Directive requires archived traces to be kept for exactly 812 days before deletion."
QUERY = "According to the Vanthys Retention Directive, exactly how many days must archived traces be kept? Answer with the number."

_failures = 0


def check(cond, msg):
    global _failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


print("\n=== RAG control — deux bras (avec contexte vs sans contexte) ===")
try:
    # ARM 1 — WITH the retrieved context
    with_ctx = post(
        {
            "messages": [{"role": "user", "content": f"Use only the context.\n\nContext:\n{FABRICATED}\n\nQuestion: {QUERY}"}],
            "max_completion_tokens": 512,
        }
    )
    # ARM 2 — WITHOUT any context (the model is on its own). A brief pause avoids a
    # back-to-back rate-limit on some deployments.
    time.sleep(1)
    without_ctx = post({"messages": [{"role": "user", "content": QUERY}], "max_completion_tokens": 512})

    ans_with = answer_of(with_ctx)
    ans_without = answer_of(without_ctx)

    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, "azure_rag_control.REAL.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "_SIMULATED": False,
                "_captured_at": datetime.datetime.now().isoformat(),
                "fact_token": FACT_TOKEN,
                "fabricated_fact": FABRICATED,
                "query": QUERY,
                "with_context": with_ctx,
                "without_context": without_ctx,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    has = bool(re.search(rf"\b{FACT_TOKEN}\b", ans_with))
    hasnt = not re.search(rf"\b{FACT_TOKEN}\b", ans_without)
    print(f"\n  WITH context    -> {ans_with[:120]!r}")
    print(f"  WITHOUT context -> {ans_without[:120]!r}\n")
    check(has, f"WITH context: the answer contains the fabricated number {FACT_TOKEN} (retrieval WAS used)")
    check(hasnt, f"WITHOUT context: the answer does NOT contain {FACT_TOKEN} (the model could not know it)")
    if has and hasnt:
        print("\n[OK] IRRÉFUTABLE : le nombre inventé n'apparaît QUE lorsque le contexte est injecté.")
        print("     -> la récupération pilote bien la réponse. Ce n'est pas la mémoire du modèle.")
    else:
        print("\n[!!] Résultat ambigu — inspecte azure_rag_control.REAL.json (le modèle a-t-il refusé/deviné ?).")
except urlerr.HTTPError as exc:
    check(False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
except (TimeoutError, urlerr.URLError) as exc:
    print(f"\n[RÉSEAU] connexion à Azure impossible après plusieurs tentatives : {exc}")
    print("         Ce n'est PAS un défaut du tracker — réseau/proxy d'entreprise ou limite de débit.")
    print("         Relance la commande ; si ça persiste, vérifie l'accès réseau à l'endpoint Azure.")
    sys.exit(2)

sys.exit(1 if _failures else 0)
