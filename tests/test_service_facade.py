"""Extra — public façade tracker.service (track_response / track_stream).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_service_facade.py

The façade is the high-level entry point: normalize a response, attach it to a trace, and
fan out best-effort to a repository and/or collector — without ever throwing into the caller.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.collector.client import CollectorClient  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.service import TrackingResult, track_response, track_stream  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
PAYLOAD = {"usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


adapter = OpenAIChatCompletionsAdapter()

# --- basic: normalize only ---
res = track_response(PAYLOAD, adapter, context=new_trace())
check(isinstance(res, TrackingResult), "returns a TrackingResult")
check(res.event.event_contributing_tokens == 10, "façade normalizes usage (10)")
check(res.persisted is None and res.collected is None and res.sink_errors == (), "no sinks -> no sink outcomes")

# --- attaches to a trace ---
trace = Trace(trace_id=(ctx := new_trace()).trace_id)
res = track_response(PAYLOAD, adapter, context=ctx, trace=trace)
check(trace.events == [res.event], "event attached to the trace")

# --- persists to a repository ---
path = os.path.join(tempfile.mkdtemp(prefix="tt_facade_"), "events.jsonl")
repo = FileRepository(path)
res = track_response(PAYLOAD, adapter, context=new_trace(), repository=repo)
check(res.persisted is True and len(repo.read_all()) == 1, "repository sink persists the event")


# --- a failing repository never breaks the caller ---
class _BoomRepo:
    def append(self, event):
        raise RuntimeError("disk full")


res = track_response(PAYLOAD, adapter, context=new_trace(), repository=_BoomRepo())
check(res.persisted is False, "failing repository -> persisted False")
check(any(e.startswith("repository:") for e in res.sink_errors), "failing repository recorded in sink_errors")
check(res.event.event_contributing_tokens == 10, "event still returned despite sink failure")

# --- collector sink + rejection path ---
collector = CollectorClient()
r1 = track_response(PAYLOAD, adapter, context=new_trace(), collector=collector, event_id="dup")
check(r1.collected is True and collector.pending == 1, "collector sink buffers the event")
r2 = track_response(PAYLOAD, adapter, context=new_trace(), collector=collector, event_id="dup")
check(r2.collected is False and "collector:rejected" in r2.sink_errors, "duplicate -> collector rejects, recorded")

# --- normalize options pass through ---
res = track_response(PAYLOAD, adapter, context=new_trace(), event_id="custom-id", extra_flags=["demo_flag"])
check(res.event.event_id == "custom-id", "event_id option passed through")
check("demo_flag" in res.event.data_quality_flags, "extra_flags passed through")

# --- bad payload never throws ---
res = track_response({"id": "x"}, adapter, context=new_trace())
check(
    "raw_usage_missing" in res.event.data_quality_flags and res.event.event_contributing_tokens == 0,
    "bad payload -> flagged event, no throw",
)

# --- track_stream binds the context ---
sctx = new_trace(workflow="streaming")
st = track_stream(context=sctx, provider="openai", api_surface="chat_completions", model="gpt-4o")
done = st.complete(output_tokens=120, input_tokens=300, provider_total_tokens=420)
check(done.trace_id == sctx.trace_id and done.span_id == sctx.span_id, "stream event carries the context identity")
check(done.provider == "openai" and done.event_contributing_tokens == 420, "stream complete totals correctly")
to = track_stream(context=sctx, provider="openai").timeout()
check(next(q for q in to.quantities if q.token_type == TokenType.OUTPUT).quantity is None, "stream timeout -> unknown output")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
