"""Regression (P1) — a truncated proxied stream must not pass for a complete one.

Anthropic streams usage in pieces: message_start carries the exact input, the output count
arrives only in message_delta near the end. If the upstream connection dies in between, the
proxy used to build an event from whatever the accumulator held and mark it
status="complete" / authoritative — with NO output quantity at all: output tokens that were
visibly streaming (text deltas seen) vanished silently, violating INV-6 (a lost count must be
surfaced as unknown, never omitted).

The proxy must detect the missing terminal stream marker (message_stop / [DONE] /
response.completed) and, when output flowed but its count never arrived:
  - keep the exact input it received (real, billed tokens — authoritative stays true);
  - add an OUTPUT quantity=None / UNKNOWN (reason stream_interrupted) so the loss is COUNTED
    as a loss;
  - flag stream_interrupted and record observation.status="incomplete", not "complete".
A clean stream (terminal marker seen) keeps exactly its previous behaviour.

Run: python tests/test_proxy_truncated_stream.py
"""

import json
import os
import sys
import threading
import urllib.request as urlreq
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import PrecisionLevel, TokenType, UnknownReason  # noqa: E402
from tracker.proxy.server import ProxyConfig, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


class FakeAnthropic(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        truncate = payload.get("model") == "truncate-test"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_x",
                    "model": payload.get("model"),
                    # input known immediately; output count would only arrive in message_delta
                    "usage": {"input_tokens": 321},
                },
            },
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "streaming answer..."}},
        ]
        if not truncate:
            events.append({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 55}})
            events.append({"type": "message_stop"})
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
        # truncate: connection closes here — no message_delta, no message_stop
        self.close_connection = True


def post_stream(url, payload):
    request = urlreq.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlreq.urlopen(request, timeout=5) as response:
        return response.status, response.read()


upstream_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
upstream_server.daemon_threads = True
threading.Thread(target=upstream_server.serve_forever, daemon=True).start()
upstream = f"http://127.0.0.1:{upstream_server.server_address[1]}"

store_root = os.path.join(os.getcwd(), f".test_proxy_truncated_stream_{uuid.uuid4().hex}")
os.makedirs(store_root, exist_ok=True)
store = os.path.join(store_root, "events.jsonl")
with open(store, "w", encoding="utf-8"):
    pass
proxy = None
try:
    repo = FileRepository(store)
    proxy = create_proxy_server(repo, ProxyConfig(provider="anthropic", upstream_base_url=upstream, port=0))
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{proxy.server_address[1]}"

    # --- truncated stream: input arrived, output count never did ---
    status, _ = post_stream(base + "/v1/messages", {"model": "truncate-test", "stream": True, "messages": []})
    check(status == 200, "truncated stream still relayed with HTTP 200")

    events = repo.read_all()
    check(len(events) == 1, f"one event persisted for the truncated stream (got {len(events)})")
    ev = events[0]

    inputs = [q for q in ev.quantities if q.token_type == TokenType.INPUT]
    check(inputs and inputs[0].quantity == 321, "exact input (321) kept — real billed tokens are not thrown away")
    check(ev.is_authoritative, "event stays authoritative (the input REALLY was consumed)")
    check(ev.event_contributing_tokens == 321, "contributing total counts the known input")

    outputs = [q for q in ev.quantities if q.token_type == TokenType.OUTPUT]
    check(bool(outputs), "an OUTPUT quantity exists — the lost output is SURFACED, not omitted (INV-6)")
    if outputs:
        check(outputs[0].quantity is None and outputs[0].precision_level == PrecisionLevel.UNKNOWN, "lost output is None/UNKNOWN")
        check(outputs[0].unknown_reason == UnknownReason.STREAM_INTERRUPTED, "unknown_reason=stream_interrupted")
    check("stream_interrupted" in ev.data_quality_flags, "stream_interrupted flag raised")
    check("unknown_quantity_present" in ev.data_quality_flags, "unknown_quantity_present flag raised (normalizer-owned)")
    check(ev.observation.get("status") == "incomplete", f"status is 'incomplete', not 'complete' (got {ev.observation.get('status')!r})")

    # --- clean stream: unchanged behaviour ---
    status2, _ = post_stream(base + "/v1/messages", {"model": "clean-test", "stream": True, "messages": []})
    events2 = repo.read_all()
    check(len(events2) == 2, "clean stream persists a second event")
    clean = events2[-1]
    check(clean.observation.get("status") == "complete", "clean stream stays status=complete")
    check("stream_interrupted" not in clean.data_quality_flags, "clean stream: no stream_interrupted flag")
    clean_outputs = [q for q in clean.quantities if q.token_type == TokenType.OUTPUT]
    check(clean_outputs and clean_outputs[0].quantity == 55, "clean stream: exact output 55 recorded as before")
finally:
    if proxy is not None:
        proxy.shutdown()
        proxy.server_close()
    upstream_server.shutdown()
    upstream_server.server_close()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
