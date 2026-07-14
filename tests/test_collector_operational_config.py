"""Collector configuration must match the operational durability contract."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import _parser  # noqa: E402
from tests._harness import make_checker  # noqa: E402

check = make_checker()

defaults = _parser({}).parse_args([])
check(defaults.host == "127.0.0.1", "collector binds to loopback by default")
check(defaults.port == 8787, "collector uses the documented default port")
check(defaults.store == "collector_events.jsonl", "collector has a deterministic default store")
check(defaults.durable is True, "collector durable persistence is enabled by default")

configured = _parser(
    {
        "TRACKER_STORE": "configured.jsonl",
        "TRACKER_HOST": "localhost",
        "TRACKER_PORT": "9876",
        "TRACKER_DURABLE": "false",
        "TRACKER_AUTH_TOKEN": "unit-token",
    }
).parse_args([])
check(configured.store == "configured.jsonl", "TRACKER_STORE configures the collector store")
check(configured.host == "localhost", "TRACKER_HOST configures the collector host")
check(configured.port == 9876, "TRACKER_PORT configures the collector port")
check(configured.durable is False, "TRACKER_DURABLE=false explicitly disables fsync")
check(configured.auth_token == "unit-token", "TRACKER_AUTH_TOKEN configures collector authentication")

overridden = _parser({"TRACKER_DURABLE": "true"}).parse_args(["--no-durable"])
check(overridden.durable is False, "--no-durable overrides the environment")

try:
    _parser({"TRACKER_DURABLE": "sometimes"})
except ValueError as exc:
    invalid_boolean_rejected = "TRACKER_DURABLE" in str(exc)
else:
    invalid_boolean_rejected = False
check(invalid_boolean_rejected, "invalid TRACKER_DURABLE values fail clearly")

sys.exit(check.report("RESULT test_collector_operational_config"))
