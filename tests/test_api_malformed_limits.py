"""Falsify bounded collector parsing without recursing over attacker input.

Run: scripts/_python.cmd tests/test_api_malformed_limits.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import uuid
from urllib import error as url_error
from urllib import request as url_request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def post(base: str, raw_body: bytes) -> tuple[int, dict[str, object]]:
    request = url_request.Request(
        base + "/v1/events",
        data=raw_body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with url_request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except url_error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def health(base: str) -> int:
    with url_request.urlopen(base + "/healthz", timeout=5) as response:
        return response.status


root = os.path.abspath(f".test_api_malformed_limits_{uuid.uuid4().hex}")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(root, exist_ok=True)
repository = FileRepository(os.path.join(root, "events.jsonl"))
server = create_server(
    repository,
    "127.0.0.1",
    0,
    max_body_bytes=256,
    max_json_depth=32,
)
base = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    deeply_nested = ("[" * 33 + "0" + "]" * 33).encode()
    code, body = post(base, deeply_nested)
    check(code == 400, "over-deep JSON is rejected with 400")
    check(body == {"error": "json_too_deep"}, "over-deep JSON has a stable error body")
    check(health(base) == 200, "worker stays healthy after over-deep JSON")

    code, body = post(base, json.dumps({"padding": "[" * 100}).encode())
    check(code == 200 and body["acked"] == [], "brackets inside JSON strings do not consume depth")

    oversized = json.dumps({"padding": "x" * 300}).encode()
    code, body = post(base, oversized)
    check(code == 413, "oversized body is rejected before parsing")
    check(body == {"error": "payload_too_large"}, "oversized body has a stable error body")
    check(health(base) == 200, "worker stays healthy after oversized body")
    check(repository.read_all() == [], "rejected payloads never reach storage")
finally:
    server.shutdown()
    server.server_close()
    shutil.rmtree(root, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
raise SystemExit(1 if _failures else 0)
