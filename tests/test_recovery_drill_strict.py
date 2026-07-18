"""The recovery drill must never pass after silently skipping ledger corruption."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import uuid
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import recovery_drill  # noqa: E402
from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or root / f".test_recovery_strict_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)
script = root / "scripts" / "recovery_drill.py"


def event() -> TokenEvent:
    return TokenEvent(
        event_id="recovery-event",
        request_correlation_id="recovery-request",
        trace_id="recovery-trace",
        span_id="recovery-span",
        provider="test",
        quantities=[
            TokenQuantity(
                token_type=TokenType.INPUT,
                quantity=7,
                precision_level=PrecisionLevel.EXACT,
                usage_source=UsageSource.PROVIDER_RESPONSE,
                additivity=Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        observation={"authoritative": True, "status": "complete"},
    )


def run(path: Path) -> tuple[int, dict]:
    old_argv = sys.argv
    output = io.StringIO()
    drill_work = work / "drill"
    try:
        sys.argv = [str(script), "--source", str(path), "--work-dir", str(drill_work), "--json"]
        with redirect_stdout(output):
            result = recovery_drill.main()
    finally:
        sys.argv = old_argv
    return result, json.loads(output.getvalue())


valid = work / "valid.jsonl"
FileRepository(str(valid)).append(event())
valid_result, valid_summary = run(valid)
check(
    valid_result == 0 and valid_summary["passed"] is True,
    f"valid ledger passes the recovery drill ({valid_summary})",
)
check(valid_summary["checks"][0]["name"] == "source_validation", "strict source validation is the first gate")
retention_check = next(item for item in valid_summary["checks"] if item["name"] == "archive_first_retention")
check(retention_check["passed"] is True, "recovery drill proves archive-first retention on its strict snapshot")
check("canonical total 7 -> 7" in retention_check["detail"], "retention drill reconciles the canonical total before and after")

for label, corrupt_bytes in (
    ("malformed_middle", valid.read_bytes() + b"{not-json}\n"),
    ("schema_invalid", valid.read_bytes() + b'{"event_id":"missing-required-fields"}\n'),
    ("truncated_tail", valid.read_bytes() + b'{"event_id":"unfinished"'),
):
    path = work / f"{label}.jsonl"
    path.write_bytes(corrupt_bytes)
    result, summary = run(path)
    check(result == 1 and summary["passed"] is False, f"{label} fails the drill")
    check(
        summary["checks"] == [
            {
                "name": "source_validation",
                "passed": False,
                "detail": summary["checks"][0]["detail"],
            }
        ],
        f"{label} stops before backup claims are emitted",
    )

if owned_temp:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(check.report("RESULT test_recovery_drill_strict"))
