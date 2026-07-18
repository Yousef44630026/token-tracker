"""Independent collector health probe with append-only operational evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib import request

from tracker.ops.auth_token import load_auth_token
from tracker.storage._locking import lock_for

Opener = Callable[..., Any]


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _append_jsonl(path: str, payload: Mapping[str, Any], *, durable: bool = True) -> None:
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    line = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True) + "\n"
    with lock_for(target):
        with open(target, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())


def _get_json(url: str, *, auth_token: str | None, timeout: float, opener: Opener) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    req = request.Request(url, headers=headers)
    with opener(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("collector response must be a JSON object")
    return payload


def check_collector(
    *,
    base_url: str,
    health_log: str,
    alert_log: str,
    auth_token: str | None = None,
    timeout: float = 3.0,
    opener: Opener = request.urlopen,
    durable: bool = True,
) -> dict[str, Any]:
    """Probe health and stats, append evidence, and return the redacted sample."""
    started = time.perf_counter()
    sample: dict[str, Any] = {
        "timestamp": _timestamp(),
        "base_url": base_url.rstrip("/"),
        "healthy": False,
    }
    try:
        health = _get_json(f"{base_url.rstrip('/')}/healthz", auth_token=auth_token, timeout=timeout, opener=opener)
        stats = _get_json(
            f"{base_url.rstrip('/')}/v1/stats?summary=1",
            auth_token=auth_token,
            timeout=timeout,
            opener=opener,
        )
        sample.update(
            {
                "healthy": health.get("status") == "ok",
                "status": str(health.get("status", "unknown")),
                "events": int(stats.get("events", 0)),
                "total": int(stats.get("total", 0)),
            }
        )
        observed_fingerprint = health.get("runtime_fingerprint")
        if isinstance(observed_fingerprint, str) and observed_fingerprint:
            sample["runtime_fingerprint"] = observed_fingerprint
        if not sample["healthy"]:
            sample["error_type"] = "unhealthy_status"
    except Exception as exc:  # noqa: BLE001 - operational monitor must emit a bounded signal
        sample.update({"status": "offline", "error_type": type(exc).__name__})
    sample["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    _append_jsonl(health_log, sample, durable=durable)
    if not sample["healthy"]:
        _append_jsonl(alert_log, {**sample, "alert": "collector_unavailable"}, durable=durable)
    return sample


def _default_paths(environment: Mapping[str, str]) -> tuple[str, str]:
    store = environment.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl")
    root = os.path.dirname(os.path.abspath(store))
    return (
        environment.get("TRACKER_HEALTH_LOG", os.path.join(root, "health", "collector-health.jsonl")),
        environment.get("TRACKER_ALERT_LOG", os.path.join(root, "health", "collector-alerts.jsonl")),
    )


def main(argv: list[str] | None = None) -> None:
    environment = os.environ
    default_health_log, default_alert_log = _default_paths(environment)
    host = environment.get("TRACKER_HOST", "127.0.0.1")
    port = environment.get("TRACKER_PORT", "8787")
    parser = argparse.ArgumentParser(description="Probe the AI token collector and append redacted health evidence")
    parser.add_argument("--base-url", default=environment.get("TRACKER_MONITOR_URL", f"http://{host}:{port}"))
    parser.add_argument("--health-log", default=default_health_log)
    parser.add_argument("--alert-log", default=default_alert_log)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--no-durable", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    sample = check_collector(
        base_url=args.base_url,
        health_log=args.health_log,
        alert_log=args.alert_log,
        auth_token=load_auth_token(),
        timeout=args.timeout,
        durable=not args.no_durable,
    )
    if args.json:
        print(json.dumps(sample, ensure_ascii=True, sort_keys=True))
    else:
        print(f"collector monitor: status={sample['status']} healthy={sample['healthy']} latency_ms={sample['latency_ms']}")
    raise SystemExit(0 if sample["healthy"] else 1)


if __name__ == "__main__":
    main()
