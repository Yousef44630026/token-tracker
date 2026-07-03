"""Extra — collector HTTP server error paths.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_api_server_errors.py

Unknown routes -> 404, unknown methods -> 501, bad/empty bodies -> 400, a single (non-list)
event is accepted, and an all-invalid batch acks nothing — the server stays up throughout.
"""

import json
import os
import sys
import tempfile
import threading
from urllib import error as urlerr
from urllib import request as urlreq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def request(base, path, method="GET", body=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urlreq.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urlreq.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urlerr.HTTPError as exc:
        return exc.code, None


def event_dict(eid, out):
    return {
        "event_id": eid,
        "request_correlation_id": "r",
        "trace_id": "t",
        "span_id": "s",
        "quantities": [
            {
                "token_type": "output",
                "quantity": out,
                "precision_level": "exact",
                "usage_source": "provider_response",
                "additivity": "total_contributing",
            }
        ],
        "provider_total_tokens": out,
    }


repo = FileRepository(os.path.join(tempfile.mkdtemp(prefix="tt_apierr_"), "events.jsonl"))
server = create_server(repo, "127.0.0.1", 0)
base = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    # unknown routes
    check(request(base, "/nope")[0] == 404, "GET unknown route -> 404")
    check(request(base, "/v1/unknown", method="POST", body="[]")[0] == 404, "POST unknown route -> 404")

    # unknown method on a known path -> 501 (BaseHTTPRequestHandler default)
    check(request(base, "/v1/events", method="PUT", body="[]")[0] == 501, "unsupported method -> 501")

    # bad bodies
    check(request(base, "/v1/events", method="POST", body="{not json")[0] == 400, "malformed body -> 400")
    check(request(base, "/v1/events", method="POST", body=b"")[0] == 400, "empty body -> 400")

    # deeply nested JSON raises RecursionError (not a ValueError subclass) from json.loads;
    # must be handled as a graceful 400, not an unhandled crash that kills the connection
    deeply_nested = "[" * 3000 + "]" * 3000
    check(
        request(base, "/v1/events", method="POST", body=deeply_nested)[0] == 400,
        "deeply nested JSON (RecursionError) -> 400, not an unhandled crash",
    )
    check(request(base, "/healthz")[0] == 200, "server still healthy right after the deeply-nested-JSON request")

    # a single (non-list) event is wrapped and accepted
    code, body = request(base, "/v1/events", method="POST", body=json.dumps(event_dict("single", 7)))
    check(code == 200 and body["acked"] == ["single"], "single event dict accepted")

    # an all-invalid batch acks nothing, server stays up
    code, body = request(base, "/v1/events", method="POST", body=json.dumps([{"bad": 1}, {"also": 2}]))
    check(code == 200 and body["acked"] == [], "all-invalid batch -> acks nothing (200)")

    # mixed valid/invalid -> only valid acked
    code, body = request(base, "/v1/events", method="POST", body=json.dumps([event_dict("ok1", 3), {"bad": 1}, event_dict("ok2", 4)]))
    check(code == 200 and body["acked"] == ["ok1", "ok2"], "mixed batch acks only valid items")

    # server still healthy after all the bad requests
    check(request(base, "/healthz")[0] == 200, "server still healthy after error paths")
    check(request(base, "/v1/stats")[1]["total"] == 7 + 3 + 4, "stats reflect only the accepted events (14)")
finally:
    server.shutdown()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
