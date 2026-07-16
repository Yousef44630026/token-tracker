"""Scheduled Claude import must be authenticated, incremental, and fail closed."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import threading
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server  # noqa: E402
from scripts import import_claude_to_collector  # noqa: E402
from tests._harness import make_checker  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or root / f".test_claude_import_operational_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)
claude_home = work / "claude"
project = claude_home / "projects" / "project"
project.mkdir(parents=True)
session = project / "session.jsonl"
state = work / "claude-import-state.json"
store = work / "collector.jsonl"
auth_token = "unit-import-token"


def assistant_line(request_id: str, usage: dict | None) -> str:
    message = {"role": "assistant", "model": "claude-test"}
    if usage is not None:
        message["usage"] = usage
    return json.dumps(
        {
            "type": "assistant",
            "requestId": request_id,
            "sessionId": "session-test",
            "timestamp": "2026-07-16T10:00:00Z",
            "message": message,
        }
    )


def run_import(*, token: str = auth_token) -> tuple[int, dict]:
    old_argv = sys.argv
    old_claude_home = os.environ.get("CLAUDE_CONFIG_DIR")
    old_auth_token = os.environ.get("TRACKER_AUTH_TOKEN")
    output = io.StringIO()
    try:
        os.environ["CLAUDE_CONFIG_DIR"] = str(claude_home)
        os.environ["TRACKER_AUTH_TOKEN"] = token
        sys.argv = [
            str(root / "scripts" / "import_claude_to_collector.py"),
            "--collector",
            base,
            "--state-file",
            str(state),
            "--json",
        ]
        with redirect_stdout(output):
            result = import_claude_to_collector.main()
    finally:
        sys.argv = old_argv
        if old_claude_home is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_claude_home
        if old_auth_token is None:
            os.environ.pop("TRACKER_AUTH_TOKEN", None)
        else:
            os.environ["TRACKER_AUTH_TOKEN"] = old_auth_token
    return result, json.loads(output.getvalue())


session.write_text(
    assistant_line(
        "request-1",
        {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 2},
    )
    + "\n",
    encoding="utf-8",
)
repository = FileRepository(str(store))
server = create_server(repository, port=0, auth_token=auth_token)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_address[1]}"

try:
    first, first_summary = run_import()
    check(first == 0, f"authenticated first import succeeds ({first_summary})")
    check(
        first_summary["sent"] == 1 and first_summary["persisted"] == 1,
        f"first event is newly persisted ({first_summary})",
    )
    check(len(repository.read_all()) == 1, "collector ledger contains the first event")
    first_state = state.read_bytes()

    second, second_summary = run_import()
    check(second == 0 and second_summary["sent"] == 0, "unchanged transcript sends nothing")
    check(second_summary["import_report"]["lines_scanned"] == 0, "unchanged transcript is not rescanned")

    with session.open("a", encoding="utf-8") as handle:
        handle.write(
            assistant_line(
                "request-2",
                {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
            )
            + "\n"
        )
    wrong_auth_state = state.read_bytes()
    unauthorized, unauthorized_summary = run_import(token="wrong-token")
    check(unauthorized == 1 and unauthorized_summary["status"] == "delivery_failed", "bad auth fails delivery")
    check(state.read_bytes() == wrong_auth_state, "failed authentication does not advance the checkpoint")

    third, third_summary = run_import()
    check(third == 0 and third_summary["sent"] == 1, "correct auth retries the uncheckpointed event")
    check(len(repository.read_all()) == 2, "retry persists exactly one additional event")

    partial = assistant_line(
        "request-3",
        {"input_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
    )
    with session.open("a", encoding="utf-8") as handle:
        handle.write(partial)
    partial_state = state.read_bytes()
    incomplete, incomplete_summary = run_import()
    check(incomplete == 0 and incomplete_summary["sent"] == 0, "incomplete transcript tail is deferred")
    check(state.read_bytes() == partial_state, "checkpoint does not cross an incomplete line")
    with session.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    completed, completed_summary = run_import()
    check(completed == 0 and completed_summary["sent"] == 1, "completed tail is imported on the next run")
    check(len(repository.read_all()) == 3, "completed tail is counted once")

    with session.open("a", encoding="utf-8") as handle:
        handle.write(assistant_line("request-drift", None) + "\n")
    pre_drift_state = state.read_bytes()
    drift, drift_summary = run_import()
    check(drift == 2 and drift_summary["status"] == "format_drift", "format drift has a distinct failure exit")
    check(state.read_bytes() == pre_drift_state, "format drift never advances the checkpoint")
    check(len(repository.read_all()) == 3, "format drift never fabricates or posts an event")
    check(first_state != state.read_bytes(), "checkpoint advances after later successful imports")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    if owned_temp:
        shutil.rmtree(work, ignore_errors=True)

sys.exit(check.report("RESULT test_claude_import_operational"))
