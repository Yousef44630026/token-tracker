"""LIVE — real OpenAI Responses API call through the local tracker proxy.

Run:
  & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\live\\test_live_openai_proxy.py

This is intentionally NOT part of the default suite. It makes a REAL, billable
OpenAI API call only when OPENAI_API_KEY is present; otherwise it skips cleanly
with exit code 0.

Why this exists:
  - tests/live/test_live_openai.py validates the OpenAI adapter from a direct SDK call.
  - this test validates the same principle we use for Anthropic/Claude proxy tests:
    client -> local proxy -> provider -> JSONL event -> report/privacy checks.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.proxy.live_usage import LiveUsageTracker  # noqa: E402
from tracker.proxy.privacy import audit_store, render_privacy_audit  # noqa: E402
from tracker.proxy.report import render_summary, summarize_events  # noqa: E402
from tracker.proxy.server import ProxyConfig, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

API_KEY = os.environ.get("OPENAI_API_KEY")
if not API_KEY:
    print("[SKIP] OPENAI_API_KEY absente — aucun appel reel effectue (cout nul).")
    print("       Definis OPENAI_API_KEY puis relance ce script pour un vrai test proxy OpenAI.")
    sys.exit(0)


MODEL = os.environ.get("OPENAI_LIVE_MODEL", "chat-latest")
PROMPT = os.environ.get("OPENAI_LIVE_PROMPT", "Reply with exactly one word: OK")
STORE = PROJECT_ROOT / "tests" / "artifacts" / "openai_proxy_live.jsonl"

try:
    BUDGET_TOKENS = int(os.environ.get("OPENAI_LIVE_BUDGET_TOKENS", "50000"))
except ValueError:
    print("[FAIL] OPENAI_LIVE_BUDGET_TOKENS doit etre un entier positif.")
    sys.exit(1)

if BUDGET_TOKENS <= 0:
    print("[FAIL] OPENAI_LIVE_BUDGET_TOKENS doit etre un entier positif.")
    sys.exit(1)

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def quantity(event: TokenEvent, token_type: TokenType) -> int:
    return sum(q.quantity or 0 for q in event.quantities if q.token_type == token_type)


def output_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def post_json(url: str, body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


STORE.parent.mkdir(parents=True, exist_ok=True)
if STORE.exists():
    STORE.unlink()

repository = FileRepository(str(STORE))
live_usage = LiveUsageTracker(budget_tokens=BUDGET_TOKENS, width=28)


def on_event(event: TokenEvent) -> None:
    delta = live_usage.observe(event)
    print(
        "tracked"
        f" provider={event.provider}"
        f" surface={event.api_surface}"
        f" model={event.model or 'unknown'}"
        f" input={quantity(event, TokenType.INPUT)}"
        f" output={quantity(event, TokenType.OUTPUT)}"
        f" total={event.event_contributing_tokens}",
        flush=True,
    )
    print(live_usage.render(delta=delta), flush=True)


server = create_proxy_server(
    repository,
    ProxyConfig(provider="openai", port=0),
    on_event=on_event,
)
host, port = server.server_address[:2]
proxy_url = f"http://{host}:{port}"
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

print(f"proxy listening on {proxy_url}")
print(f"events: {STORE}")
print(f"model: {MODEL}")
print(live_usage.render())

payload: dict | None = None
try:
    payload = post_json(
        f"{proxy_url}/v1/responses",
        {
            "model": MODEL,
            "input": PROMPT,
            "max_output_tokens": 5,
            "store": False,
        },
    )
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace")
    print(f"[FAIL] OpenAI API HTTP {exc.code}: {body[:800]}")
    _failures += 1
except Exception as exc:  # noqa: BLE001 - auth/quota/network should be readable
    print(f"[FAIL] appel OpenAI via proxy a echoue: {type(exc).__name__}: {exc}")
    _failures += 1
finally:
    server.shutdown()
    server.server_close()

events = repository.read_all()
check(len(events) == 1, f"proxy a persiste un event OpenAI (got {len(events)})")

if events:
    event = events[-1]
    usage = payload.get("usage") if isinstance(payload, dict) else None
    provider_total = usage.get("total_tokens") if isinstance(usage, dict) else None

    check(event.provider == "openai", "provider == openai")
    check(event.api_surface == "responses", "api_surface == responses")
    check(event.is_authoritative, "event authoritative")
    check(quantity(event, TokenType.INPUT) > 0, "input tokens exacts > 0")
    check(quantity(event, TokenType.OUTPUT) >= 0, "output tokens exacts >= 0")
    check(
        isinstance(provider_total, int) and event.provider_total_tokens == provider_total,
        f"total provider capture == payload usage.total_tokens ({provider_total})",
    )
    check(
        event.event_total_mismatch == 0,
        f"pas de double count / mismatch=0 (got {event.event_total_mismatch})",
    )

raw_store = STORE.read_text(encoding="utf-8") if STORE.exists() else ""
check(PROMPT not in raw_store, "raw prompt non persiste dans le JSONL")
check(API_KEY not in raw_store, "API key non persistee dans le JSONL")

privacy = audit_store(str(STORE))
print()
print(render_privacy_audit(privacy))
check(privacy["passed"], "privacy audit passed")

if events:
    print()
    print(render_summary(summarize_events(events)))

if payload is not None:
    print()
    print(f"assistant output: {output_text(payload)!r}")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
