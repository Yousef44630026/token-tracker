"""Write one synthetic Codex token_count event after a short delay.

Used by CLI smoke tests to prove the live watcher can import local Codex
session events while the child process is still running. It makes no network or
provider calls.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: fake_codex_session_writer.py CODEX_HOME", file=sys.stderr)
        return 2

    home = Path(sys.argv[1])
    sessions = home / "sessions" / "2026" / "06" / "28"
    sessions.mkdir(parents=True, exist_ok=True)
    session_id = "019f0c00-0000-7000-8000-000000000001"
    path = sessions / f"rollout-2026-06-28T00-00-00-{session_id}.jsonl"
    usage = {
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
        "total_tokens": 13,
    }
    event = {
        "timestamp": "2026-06-28T00:00:00Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": usage,
                "total_token_usage": usage,
            },
        },
    }

    time.sleep(2)
    path.write_text(json.dumps(event, ensure_ascii=False) + "\n", encoding="utf-8")
    time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
