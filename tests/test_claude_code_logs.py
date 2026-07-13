"""Extra — Claude Code session-log importer: de-duplication is the whole point.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_claude_code_logs.py

A real Claude Code transcript splits ONE API turn across multiple JSONL lines (one per
content block), each repeating a verbatim copy of that turn's `usage` under the same
`requestId`. Naively counting every line would double/triple count tokens. This test proves
the importer counts each `requestId` exactly once, ignores non-assistant/malformed/no-usage
lines, and supports incremental (snapshot-based) import.
"""

import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.proxy.claude_code_logs import (  # noqa: E402
    import_new_claude_code_events,
    import_new_claude_code_events_with_report,
    snapshot_sessions,
)

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def usage_line(
    request_id,
    usage,
    *,
    msg_type="assistant",
    role="assistant",
    session_id="sess-1",
    is_sidechain=False,
    timestamp="2026-06-27T10:00:00.000Z",
):
    return json.dumps(
        {
            "type": msg_type,
            "requestId": request_id,
            "sessionId": session_id,
            "isSidechain": is_sidechain,
            "timestamp": timestamp,
            "message": (
                {"role": role, "model": "claude-opus-4-8", "usage": usage}
                if usage is not None
                else {"role": role, "model": "claude-opus-4-8"}
            ),
        }
    )


home = os.path.abspath(f".test_claude_home_{uuid.uuid4().hex}")
proj_dir = os.path.join(home, "projects", "c--fake-project")
os.makedirs(proj_dir, exist_ok=True)
session_path = os.path.join(proj_dir, "session-1.jsonl")

turn1_usage = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 800, "output_tokens": 300}
turn2_usage = {"input_tokens": 50, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 20}

lines = [
    usage_line("req-1", turn1_usage),  # thinking block
    usage_line("req-1", turn1_usage),  # text block (SAME requestId, SAME usage repeated)
    usage_line("req-1", turn1_usage),  # tool_use block (SAME requestId again)
    usage_line("req-2", turn2_usage),
    usage_line("req-3", None, role="user", msg_type="user"),  # not an assistant turn -> ignored
    "{not valid json",  # malformed -> ignored
    usage_line("req-4", None),  # assistant but no usage -> ignored
    usage_line(None, turn2_usage),  # no requestId -> ignored (cannot de-duplicate safely)
]
with open(session_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

events, report = import_new_claude_code_events_with_report(claude_home=home)

# --- THE core guarantee: one requestId (3 duplicated lines) -> exactly ONE event ---
check(len(events) == 2, f"3 duplicated req-1 lines + 1 req-2 line -> exactly 2 events (got {len(events)})")

by_span = {e.span_id: e for e in events}
check("claude-code-req-1" in by_span and "claude-code-req-2" in by_span, "events keyed by requestId, not line number")

turn1 = by_span["claude-code-req-1"]
check(turn1.provider == "anthropic" and turn1.api_surface == "messages", "provider/surface from AnthropicMessagesAdapter")
check(turn1.model == "claude-opus-4-8", "model captured")
# Anthropic cache buckets are additive contributing input (verified rule): 1000+200+800+300 = 2300
check(turn1.event_contributing_tokens == 2300, f"req-1 contributes exactly once (2300), not 3x (got {turn1.event_contributing_tokens})")
check(turn1.trace_id == "sess-1", "trace_id == sessionId")
check("claude_code_local_usage" in turn1.data_quality_flags, "flagged as claude_code_local_usage")
check(
    turn1.observation["request_id"] == "req-1" and turn1.observation["authoritative"] is True,
    "observation carries request_id + authoritative",
)

turn2 = by_span["claude-code-req-2"]
check(turn2.event_contributing_tokens == 70, "req-2 contributes 50 + 0 + 0 + 20 == 70")

total = sum(e.event_contributing_tokens for e in events)
check(total == 2370, f"grand total is 2300 + 70 == 2370, not inflated by duplicate lines (got {total})")
check(report.session_files_discovered == 1 and report.files_scanned == 1, "import report counts discovered and scanned sessions")
check(report.usage_objects == 5 and report.duplicate_request_ids == 2, "import report quantifies repeated usage objects")
check(report.missing_request_ids == 1 and report.events_imported == 2, "import report quantifies excluded and imported usage")
check(report.malformed_json_lines == 1, "import report exposes malformed transcript lines")
check(report.format_drift_suspected is False, "healthy transcript does not trip the format-drift canary")

# --- non-assistant / malformed / no-usage / no-requestId lines never produce an event ---
check(all(e.span_id not in ("claude-code-req-3", "claude-code-req-4") for e in events), "user/no-usage lines produce no event")

# --- incremental import: a snapshot before new lines only imports what's new ---
before = snapshot_sessions(claude_home=home)
with open(session_path, "a", encoding="utf-8") as f:
    f.write(
        usage_line("req-5", {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 5}) + "\n"
    )
incremental = import_new_claude_code_events(before=before, claude_home=home)
check(len(incremental) == 1 and incremental[0].event_contributing_tokens == 15, "incremental import returns only the newly appended turn")

drift_home = os.path.join(home, "drift")
drift_project = os.path.join(drift_home, "projects", "project")
os.makedirs(drift_project, exist_ok=True)
with open(os.path.join(drift_project, "session.jsonl"), "w", encoding="utf-8") as handle:
    handle.write(usage_line("req-drift", None) + "\n")
drift_events, drift_report = import_new_claude_code_events_with_report(claude_home=drift_home)
check(drift_events == [], "assistant records without usage do not fabricate events")
check(
    drift_report.format_drift_suspected
    and "assistant_records_without_usage_objects" in drift_report.warnings,
    "import canary surfaces a likely Claude transcript format drift",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(home, ignore_errors=True)
sys.exit(1 if _failures else 0)
