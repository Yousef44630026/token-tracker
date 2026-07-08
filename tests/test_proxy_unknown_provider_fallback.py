"""P0 hardening — proxy capture for a provider WITHOUT a dedicated adapter.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_proxy_unknown_provider_fallback.py

Before this change, ``_surface`` only knew openai/anthropic paths and ``create_adapter`` was
strict, so proxying any other provider (groq, together, an OpenAI-compatible gateway...)
passed traffic through UNMEASURED — a silent capture loss, the exact failure mode the
generic fallback adapter exists to close. The proxy must now: recognize the common path
shapes for any provider, resolve via ``create_adapter_with_fallback``, and persist the call
open-captured / closed-counted (quantities unverified, contributing 0, real counts kept).
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType  # noqa: E402
from tracker.proxy.cli import _parser  # noqa: E402
from tracker.proxy.server import ProxyConfig, _surface, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()

# --- 0. CLI: the unknown-provider capability is reachable from the command line -----------
try:
    args = _parser().parse_args(["serve", "--provider", "groq", "--upstream", "http://127.0.0.1:9"])
    cli_ok = args.provider == "groq" and args.upstream == "http://127.0.0.1:9"
except SystemExit:
    cli_ok = False
check(cli_ok, "CLI accepts --provider groq with an explicit --upstream")

try:
    ProxyConfig(provider="groq")
    config_guard = False
except ValueError:
    config_guard = True
check(config_guard, "ProxyConfig still refuses an unknown provider WITHOUT an explicit upstream")

# --- 1. _surface: generic path shapes resolve for ANY provider ----------------------------
check(_surface("groq", "/openai/v1/chat/completions") == "chat_completions", "_surface: groq chat path resolves")
check(_surface("together", "/v1/responses") == "responses", "_surface: unknown-provider responses path resolves")
check(_surface("someproxy", "/v1/messages") == "messages", "_surface: unknown-provider messages path resolves")
check(_surface("groq", "/v1/models") is None, "_surface: a non-usage path still yields None")
# unchanged existing behavior
check(_surface("openai", "/v1/chat/completions") == "chat_completions", "_surface: openai unchanged")
check(_surface("anthropic", "/v1/messages") == "messages", "_surface: anthropic unchanged")


# --- 2. End-to-end: proxying an unknown provider persists an open-captured event ----------
class FakeGroq(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            request_payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeError):
            request_payload = {}
        if request_payload.get("stream"):
            # OpenAI-compatible SSE: delta chunks, then a final usage chunk, then [DONE].
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunks = [
                {"id": "chatcmpl-groq-2", "choices": [{"delta": {"content": "he"}}]},
                {"id": "chatcmpl-groq-2", "choices": [{"delta": {"content": "llo"}}]},
                {
                    "id": "chatcmpl-groq-2",
                    "model": "llama-4-scout",
                    "choices": [],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
                },
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        payload = json.dumps(
            {
                "id": "chatcmpl-groq-1",
                "model": "llama-4-scout",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 8, "total_tokens": 29},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


upstream_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGroq)
threading.Thread(target=upstream_server.serve_forever, daemon=True).start()
upstream = f"http://127.0.0.1:{upstream_server.server_address[1]}"

scratch = os.path.join(os.getcwd(), ".test_proxy_unknown_provider.jsonl")
with open(scratch, "w", encoding="utf-8"):
    pass

proxy = None
try:
    repo = FileRepository(scratch)
    proxy = create_proxy_server(repo, ProxyConfig(provider="groq", upstream_base_url=upstream, port=0))
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{proxy.server_address[1]}/openai/v1/chat/completions"

    body = json.dumps({"model": "llama-4-scout", "messages": [{"role": "user", "content": "hello"}]}).encode()
    req = urlreq.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlreq.urlopen(req, timeout=5) as response:
        status, response_body = response.status, response.read()

    check(status == 200 and json.loads(response_body)["id"] == "chatcmpl-groq-1", "traffic passes through untouched")

    events = repo.read_all()
    check(len(events) == 1, "the unknown-provider call persists exactly one event (was: silently unmeasured)")
    if events:
        event = events[0]
        by_type = {q.token_type: q for q in event.quantities}
        check(event.provider == "groq", "the event names the real provider")
        check(event.model == "llama-4-scout", "the event carries the real model")
        check(
            by_type[TokenType.INPUT].quantity == 21 and by_type[TokenType.OUTPUT].quantity == 8,
            "the provider's real counts are captured",
        )
        check(
            all(q.additivity == Additivity.UNVERIFIED for q in event.quantities),
            "every captured quantity is unverified (closed counting)",
        )
        check(event.event_contributing_tokens == 0, "the event contributes 0 to totals until verified")
        check("unverified_additivity" in event.data_quality_flags, "the caution flag is raised")
        check(event.provider_total_tokens == 29, "the raw provider total is preserved (never summed)")

    # --- 3. Streaming: an unknown provider's SSE stream is captured the same way ----------
    stream_body = json.dumps(
        {
            "model": "llama-4-scout",
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "user", "content": "hello"}],
        }
    ).encode()
    req = urlreq.Request(url, data=stream_body, headers={"Content-Type": "application/json"}, method="POST")
    with urlreq.urlopen(req, timeout=5) as response:
        streamed = response.read()
    check(b"[DONE]" in streamed, "the SSE stream passes through untouched")

    events = repo.read_all()
    check(len(events) == 2, "the streamed unknown-provider call persists its own event")
    if len(events) == 2:
        stream_event = events[-1]
        by_type = {q.token_type: q for q in stream_event.quantities}
        check(
            by_type[TokenType.INPUT].quantity == 12 and by_type[TokenType.OUTPUT].quantity == 5,
            "the final usage chunk's counts are captured",
        )
        check(
            all(q.additivity == Additivity.UNVERIFIED for q in stream_event.quantities),
            "streamed quantities are unverified too (closed counting)",
        )
        check(stream_event.event_contributing_tokens == 0, "the streamed event contributes 0 until verified")
        check(
            all(q.usage_source.value == "provider_stream_final" for q in stream_event.quantities),
            "streamed usage is stamped provider_stream_final (provenance, not response)",
        )
        check(
            all(q.precision_level == PrecisionLevel.EXACT for q in stream_event.quantities),
            "a completed stream's final usage is exact",
        )
        check(stream_event.provider_total_tokens == 17, "the streamed raw provider total is preserved")
finally:
    if proxy is not None:
        proxy.shutdown()
    upstream_server.shutdown()

sys.exit(check.report("RESULT test_proxy_unknown_provider_fallback"))
