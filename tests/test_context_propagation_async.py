"""Phase 1 / step 4 — async + thread concurrency proof.

Run: python tests/test_context_propagation_async.py

One root trace with many PARALLEL async LLM calls and a streaming span. The point is
to falsify the "highest risk" failure mode: under concurrent interleaving every unit of
work must stay attached to ITS OWN span, with the right parent/trace, and the active
context must never leak across an await or across tasks. We force interleaving with
staggered sleeps and repeat the whole gather several times to shake out races. The
in-coroutine assertions raise, so any leak fails the run loudly.

Also covers: a streaming span whose context stays bound across many chunk awaits while
other tasks run; propagation_lost surfacing inside a concurrent task; and thread-level
isolation (ContextVar is per-task/per-thread, not a shared global).
"""

import asyncio
import os
import random
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context import headers as H  # noqa: E402
from tracker.context.propagation import (  # noqa: E402
    continue_from_headers,
    current,
    new_trace,
    span,
    trace,
)

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


N_CALLS = 24
N_ROUNDS = 8


async def fake_llm_call(root):
    """Simulate one async LLM call as a child span that survives interleaving."""
    with span() as s:
        if s.parent_span_id != root.span_id:
            raise AssertionError("child span parent != root span at open")
        await asyncio.sleep(random.uniform(0.0, 0.01))  # let peers interleave
        if current() is not s:
            raise AssertionError("active context leaked across await (call)")
        await asyncio.sleep(random.uniform(0.0, 0.01))
        if current() is not s or current().trace_id != root.trace_id:
            raise AssertionError("active context drifted under concurrency")
        return s.span_id


async def fake_stream(root):
    """A streaming span: context must stay bound to ONE span across every chunk."""
    with span() as s:
        seen_spans = set()
        for _ in range(6):
            await asyncio.sleep(0.003)
            if current() is not s:
                raise AssertionError("stream span leaked across chunk await")
            seen_spans.add(current().span_id)
        return s.span_id, seen_spans


async def one_round():
    with trace(workflow="async-test", environment="dev") as root:
        results = await asyncio.gather(
            *[fake_llm_call(root) for _ in range(N_CALLS)],
            fake_stream(root),
        )
        if current() is not root:
            raise AssertionError("active context not restored to root after gather")
        call_span_ids = results[:-1]
        stream_span_id, stream_seen = results[-1]
        return root, call_span_ids, stream_span_id, stream_seen


async def run_all():
    all_ok = True
    leaked = False
    distinct_per_round = True
    stream_single = True
    for _ in range(N_ROUNDS):
        root, call_span_ids, stream_span_id, stream_seen = await one_round()
        if len(set(call_span_ids)) != N_CALLS:
            distinct_per_round = False
        if stream_span_id in call_span_ids:
            distinct_per_round = False
        if stream_seen != {stream_span_id}:
            stream_single = False
        if current() is not None:
            leaked = True
    return all_ok and not leaked, distinct_per_round, stream_single, leaked


def thread_isolation():
    """Each thread runs its own trace; no trace_id may bleed across threads."""
    results = {}
    barrier = threading.Barrier(4)

    def worker(idx):
        with trace(workflow=f"t{idx}") as root:
            barrier.wait()  # maximize overlap
            for _ in range(50):
                if current().trace_id != root.trace_id:
                    results[idx] = "leak"
                    return
            results[idx] = root.trace_id

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    trace_ids = list(results.values())
    return ("leak" not in trace_ids) and (len(set(trace_ids)) == 4)


async def lost_under_async():
    """propagation_lost still surfaces correctly inside a concurrent task."""
    good = H.inject(new_trace())
    partial = dict(good)
    del partial["X-TokenTracker-Span-Id"]

    async def task(hdrs):
        with continue_from_headers(hdrs) as res:
            await asyncio.sleep(0.005)
            return res.propagation_lost, current().span_id

    (lost1, _), (lost2, _) = await asyncio.gather(task(good), task(partial))
    return lost1 is False and lost2 is True


def main() -> int:
    ok, distinct, stream_single, leaked = asyncio.run(run_all())
    check(ok, "async gather ran with no context leak across awaits/tasks")
    check(distinct, f"each of {N_CALLS} parallel calls got its own span every round")
    check(stream_single, "streaming span stayed bound to ONE span across all chunks")
    check(not leaked, "active context restored to None after every round")

    check(thread_isolation(), "threads are isolated (no trace_id bleed across threads)")
    check(asyncio.run(lost_under_async()), "propagation_lost surfaces inside concurrent tasks")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
