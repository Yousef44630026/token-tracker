"""Azure confrontation runner — FAMILY B (streaming & supersession).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\azure_matrix_family_b.py

Confronts the streaming cases of docs/AZURE_TEST_MATRIX.md against REAL Azure OpenAI SSE
traffic, driving the tracker's actual StreamTracker (not a mock):

  B1  completed stream + include_usage -> complete_with_quantities -> EXACT, reconciles.
  B3  stream cut mid-way              -> interrupt() -> partial ESTIMATE + flags.
  B4  real final usage arrives        -> resolve_with_final_usage() -> the partial is
                                         SUPERSEDED and contributes 0; the trace = final only
                                         (the anti-double-count thesis, on real data).
  B5  no usage in time (best effort)  -> timeout() -> output None/UNKNOWN, surfaced not zeroed.

Uses the same env + /openai/v1 Bearer surface as examples/azure_matrix_family_a.py.
"""

import datetime
import json
import os
import sys
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
    print(
        "[SKIP] AZURE_OPENAI_API_KEY / (AZURE_OPENAI_RESPONSES_ENDPOINT or AZURE_OPENAI_ENDPOINT) /\n"
        "       AZURE_OPENAI_DEPLOYMENT required. No call made (zero cost)."
    )
    sys.exit(0)

_BASE = RAW_ENDPOINT.rstrip("/")
V1_BASE = _BASE if _BASE.endswith("/openai/v1") else f"{_BASE.split('/openai')[0]}/openai/v1"

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.streaming.stream_tracker import StreamTracker  # noqa: E402

adapter = AzureOpenAIChatCompletionsAdapter(deployment=CHAT)
_results: list[tuple[str, str, str]] = []


def record(case: str, verdict: str, detail: str) -> None:
    _results.append((case, verdict, detail))
    print(f"  [{verdict}] {case}: {detail}")


def save(name: str, payload: object) -> None:
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, f"{name}.REAL.json"), "w", encoding="utf-8") as f:
        json.dump({"_SIMULATED": False, "_captured_at": datetime.datetime.now().isoformat(), "captured": payload}, f, indent=2)


def open_stream(payload: dict, *, timeout: float = 60.0):
    url = f"{V1_BASE}/chat/completions"
    body = json.dumps({**payload, "model": CHAT, "stream": True}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    req = urlreq.Request(url, data=body, method="POST", headers=headers)
    return urlreq.urlopen(req, timeout=timeout)


def iter_sse(resp):
    """Yield each parsed SSE data object from a streaming response, until [DONE]."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def post_usage(payload: dict) -> dict:
    """One non-streamed call, returning its raw usage object (for the real final usage)."""
    url = f"{V1_BASE}/chat/completions"
    body = json.dumps({**payload, "model": CHAT}).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    req = urlreq.Request(url, data=body, method="POST", headers=headers)
    with urlreq.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def delta_text(chunk: dict) -> str:
    choices = chunk.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return (choices[0].get("delta") or {}).get("content") or ""
    return ""


PROMPT = [{"role": "user", "content": "Name three colors, one per line."}]

# =========================================================================================
# B1 — completed stream WITH usage: exact, provider_stream_final, reconciles
# =========================================================================================
print("\n=== B1 — stream complété avec include_usage ===")
try:
    tracker = StreamTracker.from_context(new_trace(), provider="azure_openai", api_surface="chat_completions", model=CHAT)
    usage_chunk = None
    captured = []
    with open_stream({"messages": PROMPT, "max_completion_tokens": 256, "stream_options": {"include_usage": True}}) as resp:
        for chunk in iter_sse(resp):
            captured.append(chunk)
            tracker.feed(delta_text(chunk))
            if chunk.get("usage"):
                usage_chunk = chunk
    save("azure_B1_stream_complete", captured)
    if usage_chunk is None:
        record("B1", "DISCOVERY", "aucun chunk d'usage reçu malgré include_usage")
    else:
        usage = adapter.extract_usage_from_stream_event(usage_chunk)
        ev = tracker.complete_with_quantities(
            quantities=usage.quantities, provider_total_tokens=usage.provider_total_tokens, model=usage.model
        )
        out = next((q for q in ev.quantities if q.token_type == TokenType.OUTPUT), None)
        reconciles = ev.event_total_mismatch == 0 and ev.event_contributing_tokens == ev.provider_total_tokens
        exact_final = out is not None and out.precision_level == PrecisionLevel.EXACT and out.usage_source.value == "provider_stream_final"
        record(
            "B1",
            "PASS" if (reconciles and exact_final) else "DISCOVERY",
            f"total={ev.provider_total_tokens} contrib={ev.event_contributing_tokens} exact_final={exact_final} reconciles={reconciles}",
        )
except urlerr.HTTPError as exc:
    record("B1", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# B3 + B4 — cut mid-stream (partial estimate), then real final usage supersedes it
# =========================================================================================
print("\n=== B3/B4 — interruption puis usage final (supersession réelle) ===")
try:
    tracker = StreamTracker.from_context(new_trace(), provider="azure_openai", api_surface="chat_completions", model=CHAT)
    seen = 0
    with open_stream({"messages": PROMPT, "max_completion_tokens": 256}) as resp:
        for chunk in iter_sse(resp):
            text = delta_text(chunk)
            if text:
                tracker.feed(text)
                seen += 1
                if seen >= 1:  # cut as soon as real output began streaming
                    break
    partial = tracker.interrupt()
    b3_ok = (
        partial.quantities
        and partial.quantities[-1].precision_level == PrecisionLevel.ESTIMATE
        and "partial_stream_estimate" in partial.data_quality_flags
        and "stream_interrupted" in partial.data_quality_flags
    )
    record(
        "B3",
        "PASS" if b3_ok else "DISCOVERY",
        f"partial output estimate={partial.quantities[-1].quantity} flags={partial.data_quality_flags}",
    )

    # B4: fetch the REAL final usage (same prompt, non-streamed) and resolve on the same tracker.
    real = post_usage({"messages": PROMPT, "max_completion_tokens": 256})
    save("azure_B4_final_usage", real)
    u = real.get("usage", {})
    final = tracker.resolve_with_final_usage(
        output_tokens=u.get("completion_tokens"),
        input_tokens=u.get("prompt_tokens"),
        provider_total_tokens=u.get("total_tokens"),
    )
    trace = Trace(trace_id=final.trace_id, events=[partial, final])
    total = observed_total_contributing_tokens(trace)
    b4_ok = (
        partial.superseded
        and partial.superseded_by == final.event_id
        and partial.event_contributing_tokens == 0
        and total == final.event_contributing_tokens == final.provider_total_tokens
    )
    record(
        "B4",
        "PASS" if b4_ok else "DISCOVERY",
        f"partial superseded={partial.superseded} partial_contrib={partial.event_contributing_tokens} "
        f"trace_total={total} final_total={final.provider_total_tokens} (partial+final NOT double-counted)",
    )
except urlerr.HTTPError as exc:
    record("B3", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
# B5 — timeout (best effort): no usage arrives in time -> output None/UNKNOWN, not zero
# =========================================================================================
print("\n=== B5 — timeout (best effort) -> inconnu, pas zéro ===")
try:
    tracker = StreamTracker.from_context(new_trace(), provider="azure_openai", api_surface="chat_completions", model=CHAT)
    timed_out = False
    try:
        with open_stream({"messages": PROMPT, "max_completion_tokens": 256}, timeout=0.4) as resp:
            for chunk in iter_sse(resp):
                tracker.feed(delta_text(chunk))
    except (TimeoutError, urlerr.URLError):
        timed_out = True
    if not timed_out:
        record("B5", "SKIP", "le stream a répondu avant le timeout court — timeout non déclenché (best effort)")
    else:
        ev = tracker.timeout()
        out = next((q for q in ev.quantities if q.token_type == TokenType.OUTPUT), None)
        ok = (
            out is not None and out.quantity is None and out.precision_level == PrecisionLevel.UNKNOWN and ev.event_contributing_tokens == 0
        )
        oq = out.quantity if out else "?"
        op = out.precision_level.value if out else "?"
        record("B5", "PASS" if ok else "DISCOVERY", f"output={oq} precision={op} contrib={ev.event_contributing_tokens}")
except urlerr.HTTPError as exc:
    record("B5", "DISCOVERY", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")

# =========================================================================================
print("\n" + "=" * 60)
print("RESUME FAMILLE B")
print("=" * 60)
counts: dict[str, int] = {}
for case, verdict, detail in _results:
    counts[verdict] = counts.get(verdict, 0) + 1
    print(f"  {case:4} {verdict:10} {detail}")
print("-" * 60)
print("  " + "  ".join(f"{v}={n}" for v, n in sorted(counts.items())))
sys.exit(0)
