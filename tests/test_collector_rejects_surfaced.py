"""Collector must SURFACE rejected events, never drop them silently (INV-6 spirit).

Run: python tests/test_collector_rejects_surfaced.py

A malformed item inside a batch is skipped so one bad event never fails the whole batch —
but a skipped event must be visible, not vanish. The response reports how many items were
rejected, and when a dead-letter path is configured the raw rejected items are persisted for
audit rather than lost.
"""

import json
import os
import shutil
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


def request(base, path, method="GET", body=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urlreq.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urlreq.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urlerr.HTTPError as exc:
        return exc.code, None


def event_dict(eid, out):
    return {
        "event_id": eid,
        "request_correlation_id": f"r-{eid}",
        "trace_id": "t",
        "span_id": "s",
        "quantities": [
            {
                "token_type": "output",
                "quantity": out,
                "precision_level": "exact",
                "usage_source": "provider_response",
                "overlap": "independent",
                "trust": "verified",
            }
        ],
        "provider_total_tokens": out,
        "observation": {"authoritative": True},
        "schema_version": 9,
    }


tmp = os.path.abspath(f".test_collector_rejects_surfaced_{uuid.uuid4().hex}")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
dead_letter = os.path.join(tmp, "rejected.jsonl")
repo = FileRepository(os.path.join(tmp, "events.jsonl"))
server = create_server(repo, "127.0.0.1", 0, dead_letter_path=dead_letter)
base = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    # mixed batch: two valid, one malformed -> valid acked, the reject is COUNTED, not hidden
    body = json.dumps([event_dict("ok1", 3), {"bad": 1}, event_dict("ok2", 4)])
    code, resp = request(base, "/v1/events", method="POST", body=body)
    check(code == 200, "mixed batch returns 200")
    check(resp["acked"] == ["ok1", "ok2"], "valid items acked")
    check(resp.get("rejected") == 1, "response reports rejected count (1)")

    # all-valid batch reports zero rejects
    code, resp = request(base, "/v1/events", method="POST", body=json.dumps([event_dict("ok3", 5)]))
    check(resp.get("rejected") == 0, "all-valid batch reports rejected=0")

    # the rejected raw item was persisted to the dead-letter sink, not dropped
    check(os.path.exists(dead_letter), "dead-letter file written")
    with open(dead_letter, encoding="utf-8") as fh:
        dead_rows = [json.loads(line) for line in fh if line.strip()]
    check(len(dead_rows) == 1, "exactly one rejected item captured in dead-letter")
    check(dead_rows[0]["item"] == {"bad": 1}, "dead-letter preserves the raw rejected item")
    check("reason" in dead_rows[0], "dead-letter records a rejection reason")
finally:
    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
