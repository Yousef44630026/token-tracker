"""Azure confrontation runner — FAMILY E (double accounting: proxy vs direct).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\azure_matrix_family_e.py

The audit argument: the SAME real Azure response, measured by two INDEPENDENT paths, must
produce identical token accounting.

  - PROXY path:  the request goes through the tracker's transparent loopback proxy, which
                 relays it to Azure, captures usage inline, and returns the real response.
  - DIRECT path: the client then normalizes that SAME returned response itself.

One real call, two measurements. They must agree on every quantity, the provider total, and
the contributing total — otherwise one path is losing or inventing tokens. Because it is ONE
response measured twice (not two calls), the comparison is exact even for a reasoning model
whose output length varies run to run.

  E1  chat/completions   E3  embeddings

Same env + /openai/v1 Bearer surface as the other family runners.
"""

import datetime
import json
import os
import sys
import threading
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
EMBEDDINGS = os.environ.get("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")

if not (API_KEY and RAW_ENDPOINT and CHAT):
    print(
        "[SKIP] AZURE_OPENAI_API_KEY / (AZURE_OPENAI_RESPONSES_ENDPOINT or AZURE_OPENAI_ENDPOINT) /\n"
        "       AZURE_OPENAI_DEPLOYMENT required. No call made (zero cost)."
    )
    sys.exit(0)

_BASE = RAW_ENDPOINT.rstrip("/")
V1_BASE = _BASE if _BASE.endswith("/openai/v1") else f"{_BASE.split('/openai')[0]}/openai/v1"
# The proxy appends the client's full path (/openai/v1/...) to this base, so it must be the
# bare resource host, without the /openai/v1 suffix.
UPSTREAM = V1_BASE[: -len("/openai/v1")]

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.proxy.server import ProxyConfig, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_results: list[tuple[str, str, str]] = []


def record(case: str, verdict: str, detail: str) -> None:
    _results.append((case, verdict, detail))
    print(f"  [{verdict}] {case}: {detail}")


def save(name: str, payload: object) -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, f"{name}.REAL.json"), "w", encoding="utf-8") as f:
        json.dump({"_SIMULATED": False, "_captured_at": datetime.datetime.now().isoformat(), "response": payload}, f, indent=2)


def qmap(ev) -> dict:
    return {q.token_type.value: q.quantity for q in ev.quantities}


def confront(case: str, client_path: str, payload: dict, adapter, fixture: str) -> None:
    """Send one call through the proxy, then normalize the same returned response directly."""
    scratch = os.path.join(os.getcwd(), f".test_family_e_{case}.jsonl")
    with open(scratch, "w", encoding="utf-8"):
        pass
    captured: list = []
    repo = FileRepository(scratch)
    server = create_proxy_server(
        repo,
        ProxyConfig(provider="azure_openai", upstream_base_url=UPSTREAM, port=0),
        on_event=captured.append,
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}{client_path}"
        body = json.dumps({**payload, "model": CHAT if adapter is None else adapter.azure_deployment or CHAT}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
        req = urlreq.Request(url, data=body, method="POST", headers=headers)
        with urlreq.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read())
    except urlerr.HTTPError as exc:
        record(case, "DISCOVERY", f"proxy relay HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
        return
    finally:
        server.shutdown()

    save(fixture, response)
    if not captured:
        record(case, "DISCOVERY", "the proxy relayed the call but captured no event")
        return

    event_proxy = captured[-1]
    event_direct = normalize(response, adapter, context=new_trace())

    same_q = qmap(event_proxy) == qmap(event_direct)
    same_total = event_proxy.provider_total_tokens == event_direct.provider_total_tokens
    same_contrib = event_proxy.event_contributing_tokens == event_direct.event_contributing_tokens
    ok = same_q and same_total and same_contrib
    record(
        case,
        "PASS" if ok else "DISCOVERY",
        f"proxy={qmap(event_proxy)} total={event_proxy.provider_total_tokens} contrib={event_proxy.event_contributing_tokens} "
        f"| direct total={event_direct.provider_total_tokens} contrib={event_direct.event_contributing_tokens} "
        f"| identical_quantities={same_q}",
    )


# =========================================================================================
# E1 — chat/completions: proxy capture vs direct normalization of the same response
# =========================================================================================
print("\n=== E1 — chat: proxy vs direct (même réponse, deux mesures) ===")
chat_adapter = AzureOpenAIChatCompletionsAdapter(deployment=CHAT)
confront(
    "E1",
    "/openai/v1/chat/completions",
    {"messages": [{"role": "user", "content": "Name two colors."}], "max_completion_tokens": 256},
    chat_adapter,
    "azure_E1_proxy_vs_direct_chat",
)

# =========================================================================================
# E3 — embeddings: proxy capture vs direct normalization of the same response
# =========================================================================================
print("\n=== E3 — embeddings: proxy vs direct ===")
if not EMBEDDINGS:
    record("E3", "SKIP", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT non défini")
else:
    emb_adapter = AzureOpenAIEmbeddingsAdapter(deployment=EMBEDDINGS)
    confront(
        "E3",
        "/openai/v1/embeddings",
        {"input": "The quick brown fox jumps over the lazy dog."},
        emb_adapter,
        "azure_E3_proxy_vs_direct_embeddings",
    )

# =========================================================================================
print("\n" + "=" * 60)
print("RESUME FAMILLE E")
print("=" * 60)
counts: dict[str, int] = {}
for case, verdict, detail in _results:
    counts[verdict] = counts.get(verdict, 0) + 1
    print(f"  {case:4} {verdict:10} {detail}")
print("-" * 60)
print("  " + "  ".join(f"{v}={n}" for v, n in sorted(counts.items())))
if not counts.get("DISCOVERY"):
    print("\n[OK] Le proxy transparent et la mesure directe s'accordent EXACTEMENT sur le même appel réel.")
    print("     Deux chemins indépendants, un seul chiffre — l'argument d'audit.")
sys.exit(0)
