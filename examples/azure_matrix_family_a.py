"""Azure confrontation runner — FAMILY A (quantity grain: additivity & precision).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\azure_matrix_family_a.py

Confronts cases A1..A9 of docs/AZURE_TEST_MATRIX.md against REAL Azure OpenAI traffic. Each
case makes real (billed, tiny — max_tokens small) calls, normalizes the response through the
matching adapter, checks the DERIVED criteria (never a stored total), saves the raw payload as
a *.REAL.json fixture, and prints PASS / DISCOVERY / SKIP per case.

  PASS      the real payload reconciles exactly with the adapter's assumption.
  DISCOVERY the real format differs from the hypothesis — a genuine finding, adapter needs work.
  SKIP      the deployment for this case is not configured (no call, no cost).

Calls the /openai/v1 unified surface with Bearer auth (deployment name in the body's "model").

Environment (set in the SAME terminal, PowerShell):
    $env:AZURE_OPENAI_API_KEY = "..."
    $env:AZURE_OPENAI_RESPONSES_ENDPOINT = "https://your-resource.services.ai.azure.com/openai/v1"
      (AZURE_OPENAI_ENDPOINT also accepted; /openai/v1 is appended if absent)
    $env:AZURE_OPENAI_DEPLOYMENT = "gpt-5-mini"   # a modern chat model: A1,A2,A3,A4,A5,A7,A9
  Optional (absent -> that case SKIPs, no cost):
    $env:AZURE_OPENAI_REASONING_DEPLOYMENT  = "gpt-5-mini"              # A4,A5 (defaults to chat)
    $env:AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT = "text-embedding-3-large"  # A6

A modern chat model (gpt-5 / gpt-4o / o-series) covers A1-A5,A7,A9 alone — gpt-5/o-series
reason, so A4/A5 need no separate deployment. Values may also live in a repo-root .env.
"""

import base64
import datetime
import json
import os
import struct
import sys
import zlib
from urllib import error as urlerr
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "realistic")
sys.path.insert(0, ROOT)


def _load_dotenv() -> None:
    """Best-effort: populate os.environ from a repo-root .env for keys not already set."""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
RAW_ENDPOINT = os.environ.get("AZURE_OPENAI_RESPONSES_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
CHAT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
# gpt-5 / o-series chat models are themselves reasoners, so REASONING defaults to the chat
# deployment: A4/A5 run against it and surface reasoning_tokens with no extra deployment.
REASONING = os.environ.get("AZURE_OPENAI_REASONING_DEPLOYMENT") or CHAT
EMBEDDINGS = os.environ.get("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")
VISION = os.environ.get("AZURE_OPENAI_VISION_DEPLOYMENT") or CHAT

if not (API_KEY and RAW_ENDPOINT and CHAT):
    print(
        "[SKIP] AZURE_OPENAI_API_KEY / (AZURE_OPENAI_RESPONSES_ENDPOINT or AZURE_OPENAI_ENDPOINT) /\n"
        "       AZURE_OPENAI_DEPLOYMENT required. No call made (zero cost). See the docstring."
    )
    sys.exit(0)

# Target the /openai/v1 unified surface with Bearer auth (the shape the key test proved works).
# On this surface a call carries the deployment name in the body's "model" field, not the path.
_BASE = RAW_ENDPOINT.rstrip("/")
V1_BASE = _BASE if _BASE.endswith("/openai/v1") else f"{_BASE.split('/openai')[0]}/openai/v1"

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

# A shared prefix must exceed ~1024 prompt tokens for Azure's automatic prompt cache to engage.
_CACHE_PREFIX = "You are a meticulous accounting assistant. Follow these standing instructions precisely. " * 120

_results: list[tuple[str, str, str]] = []  # (case, verdict, detail)


def record(case: str, verdict: str, detail: str) -> None:
    _results.append((case, verdict, detail))
    print(f"  [{verdict}] {case}: {detail}")


def post(model: str, path: str, payload: dict) -> dict:
    # /openai/v1 surface: deployment name goes in the body's "model", auth is Bearer, no api-version.
    url = f"{V1_BASE}/{path}"
    body = json.dumps({**payload, "model": model}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    req = urlreq.Request(url, data=body, method="POST", headers=headers)
    with urlreq.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def save(name: str, deployment: str, payload: dict) -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, f"{name}.REAL.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "_SIMULATED": False,
                "_captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "_deployment": deployment,
                "_surface": "openai/v1",
                "response": payload,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def chat_event(payload: dict):
    return normalize(payload, AzureOpenAIChatCompletionsAdapter(deployment=CHAT), context=new_trace())


def _gray_png_b64(n: int) -> str:
    """Return base64 of a valid n×n 8-bit gray PNG, built with the stdlib (always parses)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0)  # RGB, 8-bit
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * n for _ in range(n))  # each row: filter 0 + gray pixels
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def qty(event, token_type):
    q = next((x for x in event.quantities if x.token_type == token_type), None)
    return q


def reconciles(event) -> bool:
    return event.event_total_mismatch == 0 and event.event_contributing_tokens == event.provider_total_tokens


def qv(q):
    return q.quantity if q else None


def recon(event) -> str:
    return f"total={event.provider_total_tokens} contrib={event.event_contributing_tokens} reconciles={reconciles(event)}"


# =========================================================================================
# A1 — simple call: input+output exact, total_contributing, reconciles, no quality flags
# =========================================================================================
print("\n=== A1 — appel simple ===")
try:
    msg = [{"role": "user", "content": "Reply in one word: hello"}]
    payload = post(CHAT, "chat/completions", {"messages": msg, "max_completion_tokens": 512})
    save("azure_A1_simple", CHAT, payload)
    ev = chat_event(payload)
    inp, out = qty(ev, TokenType.INPUT), qty(ev, TokenType.OUTPUT)
    ok = (
        inp is not None
        and out is not None
        and inp.precision_level == PrecisionLevel.EXACT
        and inp.additivity == Additivity.TOTAL_CONTRIBUTING
        and reconciles(ev)
        and not ev.data_quality_flags
    )
    record(
        "A1",
        "PASS" if ok else "DISCOVERY",
        f"input={qv(inp)} output={qv(out)} {recon(ev)} flags={ev.data_quality_flags or '-'}",
    )
except urlerr.HTTPError as exc:
    record("A1", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A2/A3 — cache miss then hit: two calls, same long prefix. call2 cached_input > 0,
# subtotal_of input, and STILL reconciles (cache contributes 0 — no double count).
# =========================================================================================
print("\n=== A2/A3 — cache miss puis hit (prefixe partage >= 1024 tokens) ===")
try:
    cache_req = lambda: {  # noqa: E731
        "messages": [
            {"role": "system", "content": _CACHE_PREFIX},
            {"role": "user", "content": "Reply in one word: ok"},
        ],
        "max_completion_tokens": 512,
    }
    p1 = post(CHAT, "chat/completions", cache_req())
    p2 = post(CHAT, "chat/completions", cache_req())  # immediate repeat -> cache should hit
    save("azure_A2_cache_call1", CHAT, p1)
    save("azure_A3_cache_call2", CHAT, p2)
    ev1, ev2 = chat_event(p1), chat_event(p2)
    c1 = qty(ev1, TokenType.CACHED_INPUT)
    c2 = qty(ev2, TokenType.CACHED_INPUT)
    c1v = c1.quantity if c1 else 0
    c2v = c2.quantity if c2 else 0
    hit = c2v > 0 and (c2 is None or c2.additivity == Additivity.SUBTOTAL_OF) and reconciles(ev2)
    record(
        "A2",
        "PASS" if (hit and reconciles(ev1)) else "DISCOVERY",
        f"call2 cached={c2v} subtotal_of={c2.subtotal_of if c2 else '-'} reconciles={reconciles(ev2)}",
    )
    record(
        "A3",
        "PASS" if c2v > 0 and c2v >= c1v and reconciles(ev1) and reconciles(ev2) else "DISCOVERY",
        (
            f"cached call1={c1v} -> call2={c2v} "
            f"({'miss->hit observed' if c2v > c1v else 'cache already warm/stable'}; "
            f"both reconcile={reconciles(ev1) and reconciles(ev2)})"
        ),
    )
except urlerr.HTTPError as exc:
    record("A2", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A4 — reasoning (o-series): reasoning subtotal_of output > 0, reconciles, type purity
# =========================================================================================
print("\n=== A4 — reasoning (o-series) ===")
if not REASONING:
    record("A4", "SKIP", "AZURE_OPENAI_REASONING_DEPLOYMENT non defini")
else:
    try:
        payload = post(
            REASONING,
            "chat/completions",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "If 3 machines make 3 widgets in 3 minutes, how long for 100 machines to make 100 widgets? One word.",
                    }
                ],
                "max_completion_tokens": 2000,
            },
        )
        save("azure_A4_reasoning", REASONING, payload)
        ev = normalize(payload, AzureOpenAIChatCompletionsAdapter(deployment=REASONING), context=new_trace())
        r = qty(ev, TokenType.REASONING)
        forbidden = {"partial_output_observed", "estimated_input", "estimated_output"}
        purity = all(q.token_type.value not in forbidden for q in ev.quantities)
        ok = (
            r is not None
            and r.quantity
            and r.additivity == Additivity.SUBTOTAL_OF
            and r.subtotal_of == "output"
            and reconciles(ev)
            and purity
        )
        record(
            "A4",
            "PASS" if ok else "DISCOVERY",
            f"reasoning={qv(r)} subtotal_of={r.subtotal_of if r else '-'} type_purity={purity} {recon(ev)}",
        )
    except urlerr.HTTPError as exc:
        record("A4", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A5 — cache + reasoning combined: BOTH subtotals present, sum STILL == provider_total
# =========================================================================================
print("\n=== A5 — cache + reasoning combines ===")
if not REASONING:
    record("A5", "SKIP", "AZURE_OPENAI_REASONING_DEPLOYMENT non defini")
else:
    try:
        combined = {
            "messages": [
                {"role": "system", "content": _CACHE_PREFIX},
                {"role": "user", "content": "Think, then answer in one word: continue?"},
            ],
            "max_completion_tokens": 2000,
        }
        post(REASONING, "chat/completions", combined)  # warm the cache
        payload = post(REASONING, "chat/completions", combined)
        save("azure_A5_cache_plus_reasoning", REASONING, payload)
        ev = normalize(payload, AzureOpenAIChatCompletionsAdapter(deployment=REASONING), context=new_trace())
        c, r = qty(ev, TokenType.CACHED_INPUT), qty(ev, TokenType.REASONING)
        ok = c is not None and r is not None and reconciles(ev)
        record(
            "A5",
            "PASS" if ok else "DISCOVERY",
            f"cached={qv(c)} reasoning={qv(r)} {recon(ev)}",
        )
    except urlerr.HTTPError as exc:
        record("A5", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A6 — embeddings: EMBEDDING exact total_contributing, contributing == prompt_tokens
# =========================================================================================
print("\n=== A6 — embeddings ===")
if not EMBEDDINGS:
    record("A6", "SKIP", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT non defini")
else:
    try:
        payload = post(EMBEDDINGS, "embeddings", {"input": "The quick brown fox jumps over the lazy dog."})
        save("azure_A6_embeddings", EMBEDDINGS, payload)
        ev = normalize(payload, AzureOpenAIEmbeddingsAdapter(deployment=EMBEDDINGS), context=new_trace())
        e = qty(ev, TokenType.EMBEDDING)
        ok = (
            e is not None and e.precision_level == PrecisionLevel.EXACT and e.additivity == Additivity.TOTAL_CONTRIBUTING and reconciles(ev)
        )
        record(
            "A6",
            "PASS" if ok else "DISCOVERY",
            f"embedding={qv(e)} {recon(ev)}",
        )
    except urlerr.HTTPError as exc:
        record("A6", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A7 — image input (vision): input includes image tokens; NO invented modality quantity
# =========================================================================================
print("\n=== A7 — image en entree (vision) ===")
try:
    # A valid, self-contained gray PNG (built with the stdlib so it always parses — a 1x1
    # or malformed image gets rejected by the vision endpoint before any usage is returned).
    img_data_uri = f"data:image/png;base64,{_gray_png_b64(16)}"
    payload = post(
        VISION,
        "chat/completions",
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "One word: what color is this image?"},
                        {"type": "image_url", "image_url": {"url": img_data_uri}},
                    ],
                }
            ],
            "max_completion_tokens": 512,
        },
    )
    save("azure_A7_vision", VISION, payload)
    ev = normalize(payload, AzureOpenAIChatCompletionsAdapter(deployment=VISION), context=new_trace())
    inp = qty(ev, TokenType.INPUT)
    img = qty(ev, TokenType.IMAGE_INPUT)
    # Success = input captured, reconciles, and image_input only appears if the payload really
    # reported it (never fabricated). If details are absent, img is None -> still correct.
    invented = img is not None and img.quantity is None
    ok = inp is not None and reconciles(ev) and not invented
    record(
        "A7",
        "PASS" if ok else "DISCOVERY",
        f"input={qv(inp)} image_input={img.quantity if img else 'absent(not fabricated)'} reconciles={reconciles(ev)}",
    )
except urlerr.HTTPError as exc:
    record("A7", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# A9 — truncation (small completion budget): finish_reason=length, usage STILL exact, no flag
# =========================================================================================
print("\n=== A9 — reponse tronquee (budget de completion court) ===")
try:
    payload = post(
        CHAT,
        "chat/completions",
        {"messages": [{"role": "user", "content": "Write a long paragraph about the ocean."}], "max_completion_tokens": 16},
    )
    save("azure_A9_truncated", CHAT, payload)
    ev = chat_event(payload)
    finish = (payload.get("choices") or [{}])[0].get("finish_reason")
    out = qty(ev, TokenType.OUTPUT)
    ok = out is not None and out.precision_level == PrecisionLevel.EXACT and reconciles(ev) and not ev.data_quality_flags
    record(
        "A9",
        "PASS" if ok else "DISCOVERY",
        f"finish_reason={finish} output={out.quantity if out else None} exact_and_reconciles={ok} (truncation is not a quality defect)",
    )
except urlerr.HTTPError as exc:
    record("A9", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# Note: A8 (audio) is intentionally omitted from this runner — it needs a gpt-4o-audio
# deployment and an audio input; add it once that deployment exists.

# =========================================================================================
print("\n" + "=" * 60)
print("RESUME FAMILLE A")
print("=" * 60)
counts: dict[str, int] = {}
for case, verdict, detail in _results:
    counts[verdict] = counts.get(verdict, 0) + 1
    print(f"  {case:4} {verdict:10} {detail}")
print("-" * 60)
print("  " + "  ".join(f"{v}={n}" for v, n in sorted(counts.items())))
if counts.get("DISCOVERY"):
    print("\n[i] Au moins une DISCOVERY : un format reel differe de l'hypothese de l'adaptateur.")
    print("    Inspecte la fixture *.REAL.json correspondante — c'est une vraie decouverte, pas un bug de test.")
else:
    print("\n[OK] Tous les cas confrontes RECONCILIENT sur du reel Azure (hors SKIP).")

sys.exit(0)
