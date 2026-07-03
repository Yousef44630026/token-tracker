"""DEEP — real OS-thread concurrency proof for context propagation (not simulated interleaving).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_real_concurrency.py

Every other concurrency-flavored test in this suite (test_concurrency_context.py,
test_concurrency_collector.py, the async harnesses) proves properties by CONSTRUCTING a
specific interleaving by hand — useful, but it only proves the cases we thought to construct.
This file instead launches real OS threads via ThreadPoolExecutor, synchronized on a
threading.Barrier so they all start their work at the same instant, to let the OS scheduler
actually interleave them however it wants. tracker/context/propagation.py claims contextvars.
ContextVar (not a module-level mutable) keeps parallel callers from clobbering each other —
this is what makes that claim fall out of real execution instead of only out of code reading.

Two things are proven, both only meaningful under GENUINE OS-level concurrency:
  1. Zero cross-thread trace/span contamination: under a shared barrier-synchronized start,
     no thread's TokenEvent ever carries another thread's trace_id.
  2. Per-trace totals still reconcile exactly after real concurrent execution — the counting
     algebra survives contention, not just a hand-picked ordering.
"""

import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import current, span, trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
_checks = 0
_lock = threading.Lock()


def check(cond, msg):
    global _failures, _checks
    with _lock:
        _checks += 1
        print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
        if not cond:
            _failures += 1


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


# =====================================================================================
# PART 1 — barrier-synchronized threads: zero cross-thread contamination, correct totals.
# =====================================================================================
print("--- Part 1: real OS threads, barrier-synchronized start, checking for contamination ---")

N_THREADS = 40
N_CALLS_PER_THREAD = 25
SEED = int(os.environ.get("FUZZ_SEED", "2026070299"))
BARRIER_TIMEOUT_S = 30  # a resource-constrained environment must fail loudly, never hang
barrier = threading.Barrier(N_THREADS)


def worker(worker_index: int) -> dict:
    rng = random.Random(SEED + worker_index)
    adapter = OpenAIChatCompletionsAdapter()
    try:
        barrier.wait(timeout=BARRIER_TIMEOUT_S)  # every thread reaches this line before any proceeds
    except threading.BrokenBarrierError:
        return {"worker": worker_index, "contaminated": False, "barrier_timeout": True, "own_trace_id": None}
    events = []
    expected_total = 0
    with trace(business_id=f"tenant-{worker_index}", workflow="concurrency-proof") as root:
        own_trace_id = root.trace_id
        for call_index in range(N_CALLS_PER_THREAD):
            with span() as s:
                # each thread checks ITS OWN ambient context mid-flight, under contention
                mid_flight = current()
                if mid_flight is None or mid_flight.trace_id != own_trace_id:
                    return {
                        "worker": worker_index,
                        "contaminated": True,
                        "seen_trace_id": mid_flight.trace_id if mid_flight else None,
                        "own_trace_id": own_trace_id,
                    }
                inp, out = rng.randint(10, 4000), rng.randint(1, 900)
                cached = rng.randint(0, inp) if rng.random() < 0.4 else 0
                usage = {
                    "prompt_tokens": inp,
                    "completion_tokens": out,
                    "total_tokens": inp + out,
                    "prompt_tokens_details": {"cached_tokens": cached},
                }
                response = {"id": f"chatcmpl-w{worker_index}-c{call_index}", "model": "gpt-4o-mini", "usage": usage}
                ev = normalize(response, adapter, context=s)
                events.append(ev)
                expected_total += inp + out
    return {
        "worker": worker_index,
        "contaminated": False,
        "own_trace_id": own_trace_id,
        "events": events,
        "expected_total": expected_total,
    }


results = []
with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
    futures = [pool.submit(worker, i) for i in range(N_THREADS)]
    for fut in as_completed(futures):
        results.append(fut.result())

check(len(results) == N_THREADS, f"all {N_THREADS} threads completed without deadlock/exception")

timed_out = [r for r in results if r.get("barrier_timeout")]
check(
    len(timed_out) == 0,
    f"no thread hit the {BARRIER_TIMEOUT_S}s barrier timeout ({len(timed_out)} did) — a hang would fail loudly, not silently",
)

contaminated = [r for r in results if r["contaminated"]]
check(len(contaminated) == 0, f"zero cross-thread contamination detected mid-flight ({len(contaminated)} contaminated)")

usable = [r for r in results if not r["contaminated"] and not r.get("barrier_timeout")]
all_trace_ids = [r["own_trace_id"] for r in usable]
check(len(set(all_trace_ids)) == len(usable), "every thread's root trace_id is unique (no accidental sharing)")

for r in usable:
    if r["contaminated"]:
        continue
    own_id = r["own_trace_id"]
    foreign = [ev.trace_id for ev in r["events"] if ev.trace_id != own_id]
    check(not foreign, f"worker {r['worker']}: every event carries its OWN trace_id (found foreign: {foreign[:3]})")

    tr = Trace(trace_id=own_id)
    for ev in r["events"]:
        tr.add_event(ev)
    got = observed_total_contributing_tokens(tr)
    check(
        got == r["expected_total"],
        f"worker {r['worker']}: total reconciles under real concurrency ({got} != {r['expected_total']})",
    )

print(
    f"[INFO] Part 1: {N_THREADS} real OS threads x {N_CALLS_PER_THREAD} calls each = "
    f"{N_THREADS * N_CALLS_PER_THREAD} concurrent normalize() calls, seed={SEED}."
)


# =====================================================================================
# PART 2 — nested spans under concurrency: parent/child integrity survives contention.
# =====================================================================================
print("\n--- Part 2: nested parent/child spans across real threads ---")

N_THREADS_2 = 20
barrier2 = threading.Barrier(N_THREADS_2)


def nested_worker(worker_index: int) -> dict:
    try:
        barrier2.wait(timeout=BARRIER_TIMEOUT_S)
    except threading.BrokenBarrierError:
        return {"worker": worker_index, "barrier_timeout": True, "root_trace_id": None, "chain": []}
    with trace(business_id=f"agent-{worker_index}") as root:
        parent_ids_seen = []
        with span() as parent_span:
            for _ in range(10):
                with span() as child_span:
                    parent_ids_seen.append((child_span.parent_span_id, parent_span.span_id, child_span.trace_id, root.trace_id))
    return {"worker": worker_index, "root_trace_id": root.trace_id, "chain": parent_ids_seen}


results2 = []
with ThreadPoolExecutor(max_workers=N_THREADS_2) as pool:
    futures = [pool.submit(nested_worker, i) for i in range(N_THREADS_2)]
    for fut in as_completed(futures):
        results2.append(fut.result())

check(len(results2) == N_THREADS_2, f"all {N_THREADS_2} nested-span threads completed")
timed_out_2 = [r for r in results2 if r.get("barrier_timeout")]
check(len(timed_out_2) == 0, f"no thread hit the {BARRIER_TIMEOUT_S}s barrier timeout in Part 2 ({len(timed_out_2)} did)")
for r in results2:
    if r.get("barrier_timeout"):
        continue
    for child_parent_id, parent_span_id, child_trace_id, root_trace_id in r["chain"]:
        check(child_parent_id == parent_span_id, f"worker {r['worker']}: child's parent_span_id matches its actual parent under contention")
        check(
            child_trace_id == root_trace_id == r["root_trace_id"],
            f"worker {r['worker']}: trace_id preserved through nested spans under contention",
        )

print(f"[INFO] Part 2: {N_THREADS_2} threads x 10 nested child spans each, all parent/child links intact.")

print(f"\n[INFO] total checks run: {_checks}")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
