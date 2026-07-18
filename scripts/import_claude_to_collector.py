"""Import REAL Claude Code token usage into the running supervised collector.

Reads local Claude Code session transcripts (no API credit, only token facts), then POSTs
the resulting TokenEvents to the collector's /v1/events ingress in size-bounded batches.
An atomic per-file byte checkpoint makes scheduled runs incremental. The checkpoint advances
only after every event is accepted, while deterministic event ids make crash replay safe.

Usage:
  python scripts/import_claude_to_collector.py [--collector http://127.0.0.1:8787]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.ops.auth_token import load_auth_token  # noqa: E402
from tracker.proxy.claude_code_logs import (  # noqa: E402
    SessionSnapshot,
    default_claude_home,
    import_new_claude_code_events_with_report,
)

# Stay comfortably under the collector defaults (1000 events / 1 MiB body).
MAX_EVENTS_PER_BATCH = 400
MAX_BODY_BYTES = 800_000
CHECKPOINT_VERSION = 1


def _batches(payloads: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_bytes = 2  # for the enclosing [] brackets
    for payload in payloads:
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 1
        too_big = current and (len(current) >= MAX_EVENTS_PER_BATCH or current_bytes + size > MAX_BODY_BYTES)
        if too_big:
            batches.append(current)
            current = []
            current_bytes = 2
        current.append(payload)
        current_bytes += size
    if current:
        batches.append(current)
    return batches


def _default_state_file() -> Path:
    configured = os.environ.get("TRACKER_CLAUDE_IMPORT_STATE")
    if configured:
        return Path(configured).expanduser()
    store = Path(os.environ.get("TRACKER_STORE", r"C:\ai-token-tracker-data\collector_events.jsonl"))
    return store.parent / "health" / "claude-import-state.json"


def _load_checkpoint(path: Path) -> SessionSnapshot:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != CHECKPOINT_VERSION:
        raise ValueError("unsupported checkpoint format or version")
    offsets = payload.get("offsets")
    if not isinstance(offsets, dict):
        raise ValueError("checkpoint offsets must be an object")
    checkpoint: SessionSnapshot = {}
    for session_path, offset in offsets.items():
        if not isinstance(session_path, str) or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("checkpoint contains an invalid path or byte offset")
        checkpoint[session_path] = offset
    return checkpoint


def _write_checkpoint(path: Path, offsets: SessionSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "version": CHECKPOINT_VERSION,
        "updated_at": datetime.now(UTC).isoformat(),
        "offsets": dict(sorted(offsets.items())),
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _post(url: str, batch: list[dict], *, auth_token: str | None) -> dict[str, Any]:
    body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (loopback collector)
        parsed = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("collector response is not an object")
    acked = parsed.get("acked", parsed.get("accepted", []))
    persisted = parsed.get("persisted", [])
    rejected = parsed.get("rejected", 0)
    if not isinstance(acked, list) or not all(isinstance(value, str) for value in acked):
        raise ValueError("collector response has invalid acked ids")
    if not isinstance(persisted, list) or not all(isinstance(value, str) for value in persisted):
        raise ValueError("collector response has invalid persisted ids")
    if not isinstance(rejected, int) or isinstance(rejected, bool) or rejected < 0:
        raise ValueError("collector response has invalid rejected count")
    return {"acked": acked, "persisted": persisted, "rejected": rejected}


def _emit(summary: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return
    print(f"Claude home : {summary['claude_home']}")
    print(f"Checkpoint  : {summary['state_file']}")
    print("Import report: " + json.dumps(summary["import_report"], ensure_ascii=False, sort_keys=True))
    if summary["status"] == "format_drift":
        print("FORMAT DRIFT SUSPECTED: checkpoint not advanced; no events posted.")
    elif summary["status"] == "delivery_failed":
        print(f"DELIVERY FAILED: {summary['detail']}; checkpoint not advanced.")
    elif summary["status"] != "ok":
        print(f"IMPORT FAILED ({summary['status']}): {summary['detail']}; checkpoint not advanced.")
    else:
        print(
            f"Done. Sent {summary['sent']} new events; collector accepted {summary['acked']} "
            f"and newly persisted {summary['persisted']}."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collector", default="http://127.0.0.1:8787")
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    state_file = (args.state_file or _default_state_file()).expanduser().resolve()
    claude_home = default_claude_home()
    try:
        before = _load_checkpoint(state_file)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        summary = {
            "status": "checkpoint_invalid",
            "timestamp": datetime.now(UTC).isoformat(),
            "claude_home": str(claude_home),
            "state_file": str(state_file),
            "import_report": {},
            "sent": 0,
            "acked": 0,
            "persisted": 0,
            "detail": f"{type(exc).__name__}: {exc}",
        }
        _emit(summary, as_json=args.json)
        return 1

    events, report = import_new_claude_code_events_with_report(before=before)
    summary: dict[str, Any] = {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "claude_home": str(claude_home),
        "state_file": str(state_file),
        "import_report": report.to_dict(),
        "sent": 0,
        "acked": 0,
        "persisted": 0,
        "detail": "",
    }
    if report.format_drift_suspected:
        summary["status"] = "format_drift"
        summary["detail"] = ",".join(report.warnings)
        _emit(summary, as_json=args.json)
        return 2

    if not events:
        if report.checkpoint != before:
            try:
                _write_checkpoint(state_file, report.checkpoint)
            except OSError as exc:
                summary["status"] = "checkpoint_write_failed"
                summary["detail"] = f"{type(exc).__name__}: {exc}"
                _emit(summary, as_json=args.json)
                return 1
        _emit(summary, as_json=args.json)
        return 0

    payloads = [event.to_dict() for event in events]
    batches = _batches(payloads)
    url = args.collector.rstrip("/") + "/v1/events"
    acked_total = 0
    persisted_total = 0
    try:
        auth_token = load_auth_token()
    except ValueError as exc:
        summary["status"] = "auth_configuration_invalid"
        summary["detail"] = str(exc)
        _emit(summary, as_json=args.json)
        return 1
    for index, batch in enumerate(batches, start=1):
        try:
            response = _post(url, batch, auth_token=auth_token)
        except (urllib.error.URLError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            summary["status"] = "delivery_failed"
            summary["detail"] = f"batch {index}/{len(batches)}: {type(exc).__name__}: {exc}"
            _emit(summary, as_json=args.json)
            return 1
        expected_ids = {payload["event_id"] for payload in batch}
        acked_ids = set(response["acked"])
        if response["rejected"] or acked_ids != expected_ids:
            summary["status"] = "delivery_failed"
            summary["detail"] = (
                f"batch {index}/{len(batches)}: rejected={response['rejected']}, "
                f"expected_acks={len(expected_ids)}, actual_acks={len(acked_ids)}"
            )
            _emit(summary, as_json=args.json)
            return 1
        acked_total += len(acked_ids)
        persisted_total += len(response["persisted"])

    summary.update(sent=len(events), acked=acked_total, persisted=persisted_total)
    try:
        _write_checkpoint(state_file, report.checkpoint)
    except OSError as exc:
        summary["status"] = "checkpoint_write_failed"
        summary["detail"] = f"{type(exc).__name__}: {exc}"
        _emit(summary, as_json=args.json)
        return 1
    _emit(summary, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
