"""Import REAL Claude Code token usage into the running supervised collector.

Reads local Claude Code session transcripts (no API credit, only token facts), then
POSTs the resulting TokenEvents to the collector's /v1/events ingress in size-bounded
batches. The collector append path de-duplicates by event_id and the importer's ids are
deterministic (session file + requestId), so this is idempotent: re-running never
double-counts. That makes it safe both as a one-shot backfill and as a scheduled job.

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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.proxy.claude_code_logs import default_claude_home, import_new_claude_code_events  # noqa: E402

# Stay comfortably under the collector defaults (1000 events / 1 MiB body).
MAX_EVENTS_PER_BATCH = 400
MAX_BODY_BYTES = 800_000


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


def _post(url: str, batch: list[dict]) -> list[str]:
    body = json.dumps(batch, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (loopback collector)
        parsed = json.loads(response.read().decode("utf-8"))
    return parsed.get("acked", parsed.get("accepted", []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collector", default="http://127.0.0.1:8787")
    args = parser.parse_args()

    print(f"Claude home : {default_claude_home()}")
    events = import_new_claude_code_events()
    print(f"Real assistant-turn events (de-duplicated by requestId): {len(events)}")
    if not events:
        print("Nothing to import.")
        return 0

    payloads = [event.to_dict() for event in events]
    batches = _batches(payloads)
    url = args.collector.rstrip("/") + "/v1/events"
    acked_total = 0
    for index, batch in enumerate(batches, start=1):
        try:
            acked = _post(url, batch)
        except urllib.error.URLError as exc:
            print(f"POST batch {index}/{len(batches)} failed: {exc}. Is the collector up at {args.collector}?")
            return 1
        acked_total += len(acked)
        print(f"batch {index}/{len(batches)}: sent {len(batch)}, acked {len(acked)}")

    print(f"\nDone. Sent {len(events)} events; collector acked {acked_total}. "
          f"The store de-duplicates by event_id, so already-present events are ignored "
          f"(check /v1/stats for the true persisted count).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
