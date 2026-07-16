"""Extra — whole-trace snapshot store (TraceFileRepository).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_trace_repository.py

Atomically saves a complete Trace (spans + events + metadata) as JSON and reloads it: the
snapshot round-trips, stores no derived totals, leaves no temp file behind, and rejects an
unsupported schema version.
"""

import glob
import json
import os
import shutil
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.span import Span  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.storage.trace_repository import TraceFileRepository  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def build_trace(tid="trace-1"):
    tr = Trace(trace_id=tid, business_id="biz", workflow="rag", environment="prod")
    tr.add_span(Span(span_id="span-1", trace_id=tid, span_type="tool", metadata={"tool_name": "search", "result_tokens": 12}))
    tr.add_event(
        TokenEvent(
            event_id="event-1",
            request_correlation_id="r1",
            trace_id=tid,
            span_id="span-1",
            provider="openai",
            api_surface="chat_completions",
            quantities=[
                TokenQuantity(TokenType.OUTPUT, 200, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
            ],
            provider_total_tokens=200,
            observation={"authoritative": True},
        )
    )
    return tr


work = os.path.join(os.getcwd(), f".test_trace_repository_{uuid.uuid4().hex}")
os.makedirs(work, exist_ok=True)
path = os.path.join(work, "trace.json")
repo = TraceFileRepository(path)

# --- missing file -> None ---
check(repo.load() is None, "load before save -> None")

# --- save then load round-trips ---
tr = build_trace()
repo.save(tr)
loaded = repo.load()
check(loaded == tr, "save -> load round-trips the whole trace")
check(loaded.spans[0].metadata["tool_name"] == "search", "span metadata survives persistence")
check(loaded.events[0].event_contributing_tokens == 200, "event derivations recompute on load")

# --- no derived totals on disk ---
with open(path, encoding="utf-8") as f:
    raw = f.read()
check("event_contributing_tokens" not in raw and "quantity_in_total" not in raw, "snapshot stores no derived totals")
check(json.loads(raw)["schema_version"] == 1, "snapshot carries a schema_version")

# --- atomic write leaves no temp file ---
leftovers = glob.glob(os.path.join(work, ".trace-*"))
check(leftovers == [], "no temp file left behind after save")

# --- overwrite keeps the latest ---
tr2 = build_trace()
tr2.add_event(
    TokenEvent(
        event_id="event-2",
        request_correlation_id="r2",
        trace_id="trace-1",
        span_id="span-1",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, 50, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        provider_total_tokens=50,
        observation={"authoritative": True},
    )
)
repo.save(tr2)
check(len(repo.load().events) == 2, "save overwrites with the latest snapshot")

# --- failed atomic replace preserves the previous valid snapshot ---
before_failed_replace = repo.load()
replacement = build_trace()
replacement.events[0].provider_total_tokens = 999
original_replace = TraceFileRepository._replace_with_retries


def fail_replace(_source, _destination):
    raise PermissionError("injected replace failure")


replace_failed = False
TraceFileRepository._replace_with_retries = staticmethod(fail_replace)
try:
    repo.save(replacement)
except PermissionError:
    replace_failed = True
finally:
    TraceFileRepository._replace_with_retries = staticmethod(original_replace)

check(replace_failed, "atomic replace failure is surfaced")
check(repo.load() == before_failed_replace, "failed replace preserves the previous valid snapshot")
check(glob.glob(os.path.join(work, ".trace-*")) == [], "failed replace cleans its temporary file")

# --- unsupported schema_version -> ValueError ---
bad_path = os.path.join(work, "bad.json")
with open(bad_path, "w", encoding="utf-8") as f:
    json.dump({"schema_version": 999, "trace": {"trace_id": "x"}}, f)
raised = False
try:
    TraceFileRepository(bad_path).load()
except ValueError:
    raised = True
check(raised, "unsupported schema_version -> ValueError")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if _failures else 0)
