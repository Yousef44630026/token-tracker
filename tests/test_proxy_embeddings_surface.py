"""Proxy capture for the /embeddings path shape (dedicated adapter or fallback).

Run: python tests/test_proxy_embeddings_surface.py

Before this change ``_surface`` did not map ``/embeddings`` at all, so an embeddings call
through the proxy passed UNMEASURED — even for openai, which has a dedicated embeddings
adapter with exact, total-contributing accounting. The path shape must resolve for every
provider: openai gets its dedicated adapter (EMBEDDING quantity, contributing), an unknown
provider gets the generic fallback (open capture, closed counting).
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.proxy.server import ProxyConfig, _surface, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()

# --- 1. _surface maps the embeddings path shape for every provider ------------------------
check(_surface("openai", "/v1/embeddings") == "embeddings", "_surface: openai embeddings path resolves")
check(_surface("groq", "/openai/v1/embeddings") == "embeddings", "_surface: unknown-provider embeddings path resolves")
check(_surface("openai", "/v1/models") is None, "_surface: non-usage paths still yield None")


# --- 2. Shared fake upstream serving an OpenAI-style embeddings response ------------------
class FakeEmbeddings(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = json.dumps(
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 8, "total_tokens": 8},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


upstream_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEmbeddings)
threading.Thread(target=upstream_server.serve_forever, daemon=True).start()
upstream = f"http://127.0.0.1:{upstream_server.server_address[1]}"


def post_embeddings(proxy_port: int) -> int:
    body = json.dumps({"model": "text-embedding-3-small", "input": "hello world"}).encode()
    req = urlreq.Request(
        f"http://127.0.0.1:{proxy_port}/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlreq.urlopen(req, timeout=5) as response:
        return response.status


def scratch_repo(name: str) -> FileRepository:
    path = os.path.join(os.getcwd(), name)
    with open(path, "w", encoding="utf-8"):
        pass
    return FileRepository(path)


openai_proxy = None
groq_proxy = None
try:
    # --- openai: the DEDICATED embeddings adapter measures exactly ------------------------
    openai_repo = scratch_repo(".test_proxy_embeddings_openai.jsonl")
    openai_proxy = create_proxy_server(openai_repo, ProxyConfig(provider="openai", upstream_base_url=upstream, port=0))
    threading.Thread(target=openai_proxy.serve_forever, daemon=True).start()
    check(post_embeddings(openai_proxy.server_address[1]) == 200, "openai embeddings traffic passes through")

    events = openai_repo.read_all()
    check(len(events) == 1, "openai embeddings call persists one event (was: silently unmeasured)")
    if events:
        event = events[0]
        embedding_q = next((q for q in event.quantities if q.token_type == TokenType.EMBEDDING), None)
        check(event.api_surface == "embeddings", "the event carries the embeddings surface")
        check(
            embedding_q is not None and embedding_q.quantity == 8,
            "the dedicated adapter captures prompt_tokens as an EMBEDDING quantity",
        )
        check(
            embedding_q is not None and embedding_q.additivity == Additivity.TOTAL_CONTRIBUTING,
            "openai embedding tokens are total_contributing (verified accounting)",
        )
        check(event.event_contributing_tokens == 8, "the embeddings event CONTRIBUTES its exact tokens")
        check(event.provider_total_tokens == 8, "the raw provider total is preserved")

    # --- unknown provider: the generic fallback captures open / counts closed -------------
    groq_repo = scratch_repo(".test_proxy_embeddings_groq.jsonl")
    groq_proxy = create_proxy_server(groq_repo, ProxyConfig(provider="groq", upstream_base_url=upstream, port=0))
    threading.Thread(target=groq_proxy.serve_forever, daemon=True).start()
    check(post_embeddings(groq_proxy.server_address[1]) == 200, "unknown-provider embeddings traffic passes through")

    events = groq_repo.read_all()
    check(len(events) == 1, "unknown-provider embeddings call persists one event")
    if events:
        event = events[0]
        check(
            all(q.additivity == Additivity.UNVERIFIED for q in event.quantities),
            "fallback-captured embedding counts are unverified (closed counting)",
        )
        check(event.event_contributing_tokens == 0, "and contribute 0 until a dedicated adapter verifies them")
        check(
            any(q.quantity == 8 for q in event.quantities),
            "while the provider's real count is preserved for the audit trail",
        )
finally:
    for server in (openai_proxy, groq_proxy):
        if server is not None:
            server.shutdown()
    upstream_server.shutdown()

sys.exit(check.report("RESULT test_proxy_embeddings_surface"))
