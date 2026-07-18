"""Extra — concurrency: thread isolation of the propagation context (INV-5).

Run: python tests/test_concurrency_context.py

The active context lives in a ContextVar, so each thread gets its own. Many threads opening
their own trace/span and normalizing concurrently must never cross-contaminate identity:
every thread's event carries that thread's own trace_id / workflow.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import current, span, trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
T = 24
PAYLOAD = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


adapter = OpenAIChatCompletionsAdapter()
results: dict[int, dict] = {}
errors: list = []
barrier = threading.Barrier(T)


def worker(tid: int) -> None:
    try:
        barrier.wait()  # maximize interleaving
        with trace(business_id=f"biz-{tid}", workflow=f"wf-{tid}"):
            with span() as sp:
                # the ambient context must be THIS thread's span
                assert current().span_id == sp.span_id
                ev = normalize(PAYLOAD, adapter)
                with span() as child:  # a nested span keeps the right parent
                    child_parent = child.parent_span_id
                results[tid] = {
                    "trace_id": ev.trace_id,
                    "span_id": ev.span_id,
                    "workflow": ev.workflow,
                    "business_id": ev.business_id,
                    "sp_trace": sp.trace_id,
                    "child_parent": child_parent,
                    "sp_id": sp.span_id,
                    "contributing": ev.event_contributing_tokens,
                }
    except Exception as exc:  # noqa: BLE001
        errors.append((tid, repr(exc)))


threads = [threading.Thread(target=worker, args=(i,)) for i in range(T)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check(errors == [], f"no thread raised (errors: {errors[:3]})")
check(len(results) == T, f"all {T} threads produced a result")

# each thread's event carries ITS OWN labels (no cross-contamination)
all_correct = all(r["workflow"] == f"wf-{tid}" and r["business_id"] == f"biz-{tid}" for tid, r in results.items())
check(all_correct, "every thread's event carries its own workflow/business_id")

# every thread saw a distinct trace, and the event attached to that thread's span
trace_ids = {r["trace_id"] for r in results.values()}
check(len(trace_ids) == T, f"{T} distinct trace_ids (no shared/clobbered context)")
check(
    all(r["trace_id"] == r["sp_trace"] and r["span_id"] == r["sp_id"] for r in results.values()), "event attached to its own thread's span"
)
check(all(r["child_parent"] == r["sp_id"] for r in results.values()), "nested child span keeps the right parent per thread")
check(all(r["contributing"] == 15 for r in results.values()), "every thread computed the same correct total (15)")

# after all threads finish, the main thread has no leaked ambient context
check(current() is None, "no ambient context leaked into the main thread")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
