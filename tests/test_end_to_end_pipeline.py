"""Extra — full pipeline integration: payload -> adapter -> JSONL -> trace -> export.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_end_to_end_pipeline.py

Drives one (SIMULATED) OpenAI payload through every layer and asserts the contributing total
stays identical at each hop: adapter usage, assembled event, JSONL read-back, trace rollup,
and the exported CSV. If any layer disagreed, this would catch it.
"""

import csv
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
EXPECTED = 1300  # input 1000 + output 300 (cached/reasoning are subtotals -> 0)


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# 1) payload -> adapter
with open(os.path.join(FIXTURES, "openai_chat_completions_cached_reasoning.SIMULATED.json"), encoding="utf-8") as f:
    payload = json.load(f)["response"]
usage = OpenAIChatCompletionsAdapter().extract_usage_from_response(payload)
check(sum(q.quantity_in_total for q in usage.quantities) == EXPECTED, "1) adapter usage total == 1300")

# 2) adapter -> event
event = TokenEvent(
    event_id="evt-e2e",
    request_correlation_id="r-e2e",
    trace_id="t-1",
    span_id="s-1",
    provider=usage.provider,
    api_surface=usage.api_surface,
    model=usage.model,
    quantities=usage.quantities,
    provider_total_tokens=usage.provider_total_tokens,
)
check(event.event_contributing_tokens == EXPECTED, "2) assembled event total == 1300")

# 3) event -> JSONL -> read back
path = os.path.join(tempfile.mkdtemp(prefix="tt_e2e_"), "events.jsonl")
repo = FileRepository(path)
repo.append(event)
read_back = repo.read_all()
check(len(read_back) == 1 and read_back[0].event_contributing_tokens == EXPECTED, "3) JSONL read-back total == 1300")

# 4) trace rollup
trace = Trace(trace_id="t-1")
for e in read_back:
    trace.add_event(e)
check(observed_total_contributing_tokens(trace) == EXPECTED, "4) trace rollup total == 1300")

# 5) export -> CSV
out_dir = tempfile.mkdtemp(prefix="tt_e2e_out_")
paths = export_csv(trace, out_dir)
with open(paths["token_events"], newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
csv_total = sum(int(r["event_contributing_tokens"]) for r in rows)
check(csv_total == EXPECTED, "5) exported CSV total == 1300")

check(
    csv_total == observed_total_contributing_tokens(trace) == event.event_contributing_tokens == EXPECTED, "every layer agrees end-to-end"
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
