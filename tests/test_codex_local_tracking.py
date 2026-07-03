"""Codex local token-count importer regression tests."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tracker.models.enums import TokenType  # noqa: E402
from tracker.proxy.codex_logs import import_new_codex_events, snapshot_sessions  # noqa: E402
from tracker.proxy.quality import check_prompt_output  # noqa: E402

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def quantity(event, token_type: TokenType) -> int:
    return sum(q.quantity or 0 for q in event.quantities if q.token_type == token_type)


tmp_root = ROOT / "codex-local-test-dir"
tmp_root.mkdir(exist_ok=True)
home = tmp_root / f"run-{uuid.uuid4().hex}"

try:
    home.mkdir()
    sessions = home / "sessions" / "2026" / "06" / "26"
    sessions.mkdir(parents=True)
    session_id = "019f0614-b048-70c3-88e9-a03758af381b"
    rollout = sessions / f"rollout-2026-06-26T22-37-26-{session_id}.jsonl"
    rollout.write_text(
        json.dumps({"timestamp": "2026-06-26T00:00:00Z", "type": "session_meta"}) + "\n",
        encoding="utf-8",
    )

    before = snapshot_sessions(home)
    token_count = {
        "timestamp": "2026-06-26T00:00:01Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 105,
                },
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 105,
                },
            },
        },
    }
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(token_count) + "\n")

    events = import_new_codex_events(before=before, codex_home=home)
    check(len(events) == 1, "imports exactly the new Codex token_count event")
    event = events[0]
    check(event.provider == "openai" and event.api_surface == "responses", "maps to OpenAI Responses")
    check(event.model is None, "model is optional when Codex state DB is absent")
    check(quantity(event, TokenType.INPUT) == 100, "input tokens mapped")
    check(quantity(event, TokenType.CACHED_INPUT) == 10, "cached input subtotal mapped")
    check(quantity(event, TokenType.OUTPUT) == 5, "output tokens mapped")
    check(quantity(event, TokenType.REASONING) == 2, "reasoning subtotal mapped")
    check(event.event_contributing_tokens == 105, "subtotals do not double count")
    check(event.event_total_mismatch == 0, "provider total reconciles")
    check(event.observation["line_number"] == 2, "line number remains absolute after snapshot offset")
    stored = json.dumps(event.to_dict(), ensure_ascii=False)
    check("secret" not in stored.lower(), "no synthetic prompt/secret leaked")

    contamination_snapshot = snapshot_sessions(home)
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    **token_count,
                    "timestamp": "2026-06-26T00:00:02Z",
                }
            )
            + "\n"
        )
    new_session_id = "019f0614-b048-70c3-88e9-a03758af381c"
    new_rollout = sessions / f"rollout-2026-06-26T22-38-00-{new_session_id}.jsonl"
    new_rollout.write_text(
        json.dumps(
            {
                **token_count,
                "timestamp": "2026-06-26T00:00:03Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    isolated_events = import_new_codex_events(
        before=contamination_snapshot,
        codex_home=home,
        only_new_sessions=True,
    )
    check(
        len(isolated_events) == 1 and isolated_events[0].trace_id == new_session_id,
        "suite import ignores concurrent existing Codex sessions",
    )

    quality_cases = [
        check_prompt_output(
            sequence=1,
            label="Strict JSON output",
            stdout='{"provider":"anthropic","mode":"agent","status":"ready"}',
        ),
        check_prompt_output(sequence=2, label="Small reasoning", stdout="answer=399"),
        check_prompt_output(
            sequence=3,
            label="Tiny code answer",
            stdout='function initials(name) { return name; }\ninitials("A B");',
        ),
        check_prompt_output(
            sequence=4,
            label="Workspace read-only context",
            stdout="- one\n- two\n- three",
        ),
    ]
    check(all(result.passed for result in quality_cases), "varied-suite quality rules pass")

finally:
    shutil.rmtree(home, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
