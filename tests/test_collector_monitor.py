"""Collector monitor emits bounded, secret-free health and alert evidence."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.collector_monitor import check_collector  # noqa: E402

check = make_checker()


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def healthy_opener(req, timeout):
    check(req.get_header("Authorization") == "Bearer unit-secret", "monitor authenticates without persisting the token")
    if req.full_url.endswith("/healthz"):
        return FakeResponse({"status": "ok"})
    if req.full_url.endswith("/v1/stats?summary=1"):
        return FakeResponse({"events": 3, "total": 42, "traces": {}})
    raise AssertionError(req.full_url)


def offline_opener(req, timeout):
    raise OSError("unit offline")


root = os.path.abspath(f".test_collector_monitor_{uuid.uuid4().hex}")
health_log = os.path.join(root, "health.jsonl")
alert_log = os.path.join(root, "alerts.jsonl")

healthy = check_collector(
    base_url="http://127.0.0.1:8787",
    health_log=health_log,
    alert_log=alert_log,
    auth_token="unit-secret",
    opener=healthy_opener,
)
check(healthy["healthy"] is True, "healthy collector produces a passing sample")
check(healthy["events"] == 3 and healthy["total"] == 42, "monitor records bounded collector counters")
check(not os.path.exists(alert_log), "healthy probe does not create an alert")

offline = check_collector(
    base_url="http://127.0.0.1:8787",
    health_log=health_log,
    alert_log=alert_log,
    auth_token="unit-secret",
    opener=offline_opener,
)
check(offline["healthy"] is False, "offline collector produces a failing sample")
check(offline["error_type"] == "OSError", "offline sample keeps a low-cardinality error type")

health_text = open(health_log, encoding="utf-8").read()
alert_text = open(alert_log, encoding="utf-8").read()
check(len(health_text.splitlines()) == 2, "health ledger appends every probe")
check(len(alert_text.splitlines()) == 1, "alert ledger appends only failures")
check("collector_unavailable" in alert_text, "alert ledger carries a stable signal")
check("unit-secret" not in health_text + alert_text, "operational evidence never stores the auth token")

shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_collector_monitor"))
