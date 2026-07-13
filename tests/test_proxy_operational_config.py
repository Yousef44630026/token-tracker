"""Operational proxy configuration must match the documented environment contract."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.proxy.cli import _parser  # noqa: E402

check = make_checker()

configured = _parser(
    {
        "TRACKER_PROXY_HOST": "localhost",
        "TRACKER_PROXY_PORT": "9090",
        "TRACKER_PROXY_STORE": "configured.jsonl",
        "TRACKER_PROXY_DURABLE": "yes",
    }
).parse_args(["serve", "--provider", "openai"])
check(configured.host == "localhost", "TRACKER_PROXY_HOST configures the proxy host")
check(configured.port == 9090, "TRACKER_PROXY_PORT is parsed as an integer")
check(configured.store == "configured.jsonl", "TRACKER_PROXY_STORE configures the event store")
check(configured.durable is True, "durable persistence is enabled by default/config")

overridden = _parser(
    {
        "TRACKER_PROXY_HOST": "localhost",
        "TRACKER_PROXY_PORT": "9090",
        "TRACKER_PROXY_STORE": "configured.jsonl",
        "TRACKER_PROXY_DURABLE": "true",
    }
).parse_args(
    [
        "serve",
        "--provider",
        "openai",
        "--host",
        "127.0.0.1",
        "--port",
        "8088",
        "--store",
        "cli.jsonl",
        "--no-durable",
    ]
)
check(overridden.host == "127.0.0.1", "CLI host overrides the environment")
check(overridden.port == 8088, "CLI port overrides the environment")
check(overridden.store == "cli.jsonl", "CLI store overrides the environment")
check(overridden.durable is False, "--no-durable explicitly selects buffered persistence")

try:
    _parser({"TRACKER_PROXY_DURABLE": "sometimes"})
except ValueError as exc:
    invalid_boolean_rejected = "TRACKER_PROXY_DURABLE" in str(exc)
else:
    invalid_boolean_rejected = False
check(invalid_boolean_rejected, "invalid TRACKER_PROXY_DURABLE values fail clearly")

try:
    _parser({"TRACKER_PROXY_PORT": "not-a-port"}).parse_args(["serve", "--provider", "openai"])
except SystemExit as exc:
    invalid_port_rejected = exc.code != 0
else:
    invalid_port_rejected = False
check(invalid_port_rejected, "invalid TRACKER_PROXY_PORT values fail argument parsing")

sys.exit(check.report("RESULT test_proxy_operational_config"))
