"""Falsify silent collector code/disk skew.

Run: python tests/test_runtime_code_freshness.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from urllib import request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server  # noqa: E402
from tests._harness import make_checker  # noqa: E402
from tracker.ops.doctor import _health_evidence_check  # noqa: E402
from tracker.ops.runtime_fingerprint import runtime_fingerprint  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
root = Path(f".test_runtime_fingerprint_{uuid.uuid4().hex}").resolve()
shutil.rmtree(root, ignore_errors=True)
(root / "api").mkdir(parents=True)
(root / "tracker").mkdir()

try:
    source = root / "api" / "main.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    first = runtime_fingerprint(root)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = runtime_fingerprint(root)
    check(first != second, "runtime fingerprint changes when Python source changes")

    repository = FileRepository(str(root / "events.jsonl"))
    server = create_server(repository, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with request.urlopen(base + "/healthz", timeout=5) as response:
            health = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
    expected = runtime_fingerprint()
    check(health["runtime_fingerprint"] == expected, "collector health exposes its startup code fingerprint")

    now = dt.datetime(2026, 7, 17, 15, 0, tzinfo=dt.UTC)
    health_log = root / "health.jsonl"

    def write_health(fingerprint: str | None) -> None:
        payload = {
            "timestamp": "2026-07-17T14:59:00Z",
            "healthy": True,
            "status": "ok",
        }
        if fingerprint is not None:
            payload["runtime_fingerprint"] = fingerprint
        health_log.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    write_health(expected)
    check(_health_evidence_check(str(health_log), now=now).status == "pass", "Doctor accepts matching runtime evidence")
    write_health("0" * 64)
    mismatch = _health_evidence_check(str(health_log), now=now)
    check(mismatch.status == "fail", "Doctor fails collector code/disk skew")
    check("restart required" in mismatch.detail, "code skew failure gives the corrective action")
    write_health(None)
    check(_health_evidence_check(str(health_log), now=now).status == "warn", "legacy evidence without a fingerprint is visible")
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_runtime_code_freshness"))
