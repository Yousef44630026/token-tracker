"""Extra — collector server auth (401) + storage failure codes (500/503).

Run: python tests/test_api_auth_and_errors.py

With a bearer token configured, ingestion and stats require it (401 otherwise) while /healthz
stays public. A failing storage backend surfaces 500 (read) / 503 (write) without crashing.
"""

import json
import os
import sys
import threading
import uuid
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


def status(base, path, *, method="GET", body=None, token=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urlreq.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urlreq.urlopen(req, timeout=5) as resp:
            return resp.status
    except urlerr.HTTPError as exc:
        return exc.code


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
        "observation": {"authoritative": True},
    }


# --- bearer auth ---
auth_root = os.path.abspath(f".test_api_auth_{uuid.uuid4().hex}")
os.makedirs(auth_root, exist_ok=True)
auth_repo = FileRepository(os.path.join(auth_root, "events.jsonl"))
auth_server = create_server(auth_repo, "127.0.0.1", 0, auth_token="secret")
abase = f"http://127.0.0.1:{auth_server.server_address[1]}"
threading.Thread(target=auth_server.serve_forever, daemon=True).start()
try:
    check(status(abase, "/healthz") == 200, "/healthz stays public under auth")
    check(status(abase, "/v1/stats") == 401, "/v1/stats without token -> 401")
    check(status(abase, "/v1/stats", token="wrong") == 401, "/v1/stats with wrong token -> 401")
    check(status(abase, "/v1/stats", token="secret") == 200, "/v1/stats with correct token -> 200")
    check(status(abase, "/v1/events", method="POST", body=[event_dict("a", 5)]) == 401, "POST without token -> 401")
    check(status(abase, "/v1/events", method="POST", body=[event_dict("a", 5)], token="secret") == 200, "POST with correct token -> 200")
    check(len(auth_repo.read_all()) == 1, "only the authenticated event was stored")
finally:
    auth_server.shutdown()
    auth_server.server_close()


# --- storage failures: 500 on read, 503 on write ---
class FailingRepo:
    def read_all(self):
        raise OSError("storage read failed")

    def append_unique(self, events):
        raise OSError("storage write failed")


fail_server = create_server(FailingRepo(), "127.0.0.1", 0)
fbase = f"http://127.0.0.1:{fail_server.server_address[1]}"
threading.Thread(target=fail_server.serve_forever, daemon=True).start()
try:
    check(status(fbase, "/healthz") == 200, "/healthz works even if storage is down")
    check(status(fbase, "/v1/stats") == 500, "storage read failure -> 500")
    check(status(fbase, "/v1/events", method="POST", body=[event_dict("b", 9)]) == 503, "storage write failure -> 503")
finally:
    fail_server.shutdown()
    fail_server.server_close()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
