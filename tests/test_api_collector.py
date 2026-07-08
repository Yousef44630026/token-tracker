"""Extra — collector HTTP endpoint (stdlib http.server), full pipeline.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_api_collector.py

Spins the stdlib collector server on an ephemeral loopback port and checks the whole loop:
CollectorClient -> HTTP transport -> api.main -> FileRepository (JSONL), plus /healthz,
/v1/stats totals, malformed-body handling, and partial-batch acceptance.
"""

import json
import os
import sys
import threading
import uuid
from urllib import error as urllib_error
from urllib import request as urllib_request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server, make_http_transport  # noqa: E402
from tracker.collector.client import CollectorClient  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def event(eid, out):
    return TokenEvent(
        event_id=eid,
        request_correlation_id=f"r-{eid}",
        trace_id="t-api",
        span_id="s",
        provider="openai",
        api_surface="chat_completions",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, out, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=out,
    )


def get_json(url):
    with urllib_request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


root = os.path.abspath(f".test_api_collector_{uuid.uuid4().hex}")
os.makedirs(root, exist_ok=True)
path = os.path.join(root, "events.jsonl")
repo = FileRepository(path)
server = create_server(repo, "127.0.0.1", 0)
host, port = server.server_address
base = f"http://127.0.0.1:{port}"
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    # --- health ---
    code, body = get_json(base + "/healthz")
    check(code == 200 and body.get("status") == "ok", "/healthz returns ok")

    # --- direct transport POST of a batch ---
    transport = make_http_transport(base + "/v1/events")
    acked = transport([event("a", 100).to_dict(), event("b", 200).to_dict()])
    check(sorted(acked) == ["a", "b"], "POST batch acks both event_ids")
    check(len(repo.read_all()) == 2, "server persisted the batch to JSONL")

    # --- stats reflect contributing totals ---
    code, stats = get_json(base + "/v1/stats")
    check(code == 200 and stats["events"] == 2, "/v1/stats counts persisted events")
    check(stats["total"] == 300, "/v1/stats total == 100 + 200")
    check(stats["traces"]["t-api"] == 300, "/v1/stats per-trace total")

    # --- malformed body -> 400, server stays up ---
    bad_status = None
    try:
        req = urllib_request.Request(base + "/v1/events", data=b"{not json", headers={"Content-Type": "application/json"}, method="POST")
        urllib_request.urlopen(req, timeout=5)
    except urllib_error.HTTPError as exc:
        bad_status = exc.code
    check(bad_status == 400, "malformed JSON body -> HTTP 400 (no crash)")

    # --- partial batch: only valid items accepted ---
    acked2 = transport([event("c", 300).to_dict(), {"garbage": True}])
    check(acked2 == ["c"], "partial batch acks only the valid event")

    # --- full pipeline via CollectorClient + HTTP transport ---
    client = CollectorClient(transport)
    for i in range(5):
        client.record(event(f"k{i}", 10))
    client.flush()
    check(client.sent_total == 5, "CollectorClient delivered 5 events over HTTP")

    code, stats = get_json(base + "/v1/stats")
    check(stats["events"] == 8, "all events (2 + 1 + 5) persisted")
    check(stats["total"] == 300 + 300 + 50, "grand total reconciles end-to-end (650)")
    check(len(repo.read_all()) == 8, "repository holds every delivered event")
finally:
    server.shutdown()
    server.server_close()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
