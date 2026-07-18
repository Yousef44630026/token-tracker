"""P0 hardening — context propagation across thread pools (INV-5 / Phase 1 companion).

Run: python tests/test_context_thread_pool_propagation.py

ContextVars do NOT flow into a raw ``ThreadPoolExecutor.submit()``: the worker thread runs
with its own (empty) context, so an LLM call made inside the pool silently becomes its own
root — no ``propagation_lost`` flag, no trace linkage. That is the one place a call could
still get lost WITHOUT a flag.

This test (1) documents that stdlib behavior, then (2) requires the tracker's helpers to fix
it: ``carry_context`` (wrap one callable) and ``ContextPropagatingExecutor`` (a drop-in
ThreadPoolExecutor whose submit captures the caller's context).
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.context.propagation import current, span, trace  # noqa: E402
from tracker.context.threads import ContextPropagatingExecutor, carry_context  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()

PAYLOAD = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
adapter = OpenAIChatCompletionsAdapter()


def ambient_ids() -> tuple[str, str] | None:
    ctx = current()
    return (ctx.trace_id, ctx.span_id) if ctx is not None else None


# --- 1. Document the stdlib gap: a raw executor drops the ambient context -----------------
with trace(business_id="biz-raw"), span():
    with ThreadPoolExecutor(max_workers=1) as pool:
        seen_raw = pool.submit(ambient_ids).result()
check(seen_raw is None, "documented gap: raw ThreadPoolExecutor.submit loses the ambient context")


# --- 2. carry_context: one wrapped callable sees the submitter's exact context ------------
with trace(business_id="biz-carry"), span() as sp:
    with ThreadPoolExecutor(max_workers=1) as pool:
        seen_wrapped = pool.submit(carry_context(ambient_ids)).result()
check(
    seen_wrapped == (sp.trace_id, sp.span_id),
    "carry_context: worker sees the submitting span's trace_id/span_id",
)


# --- 3. carry_context captures at WRAP time, not call time --------------------------------
with trace(business_id="biz-bind"), span() as sp_a:
    wrapped = carry_context(ambient_ids)
seen_after_exit = wrapped()  # called outside the span: must still see sp_a
check(
    seen_after_exit == (sp_a.trace_id, sp_a.span_id),
    "carry_context: context is captured when wrapping, surviving scope exit",
)


# --- 4. ContextPropagatingExecutor: drop-in submit propagates automatically ---------------
with trace(business_id="biz-auto"), span() as sp:
    with ContextPropagatingExecutor(max_workers=1) as pool:
        seen_auto = pool.submit(ambient_ids).result()
check(
    seen_auto == (sp.trace_id, sp.span_id),
    "ContextPropagatingExecutor: submit propagates the caller's context",
)


# --- 5. No cross-contamination: N submitters, each worker sees ITS submitter's span -------
N = 16
results: dict[int, tuple[str, str] | None] = {}
expected: dict[int, tuple[str, str]] = {}
barrier = threading.Barrier(N)


def submitter(tid: int, pool: ContextPropagatingExecutor) -> None:
    with trace(business_id=f"biz-{tid}"), span() as sp:
        expected[tid] = (sp.trace_id, sp.span_id)
        barrier.wait()  # maximize interleaving before submitting
        results[tid] = pool.submit(ambient_ids).result()


with ContextPropagatingExecutor(max_workers=4) as shared_pool:
    threads = [threading.Thread(target=submitter, args=(i, shared_pool)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
check(
    all(results[i] == expected[i] for i in range(N)),
    f"no cross-contamination across {N} concurrent submitters sharing one pool",
)
check(len({expected[i] for i in range(N)}) == N, "sanity: the submitters really had distinct spans")


# --- 6. A normalized event inside the pool attaches to the submitter's trace --------------
with trace(business_id="biz-event", workflow="wf-pool"), span() as sp:
    with ContextPropagatingExecutor(max_workers=1) as pool:
        ev = pool.submit(carry_context(lambda: normalize(PAYLOAD, adapter))).result()
check(ev.trace_id == sp.trace_id, "normalized event in the pool carries the submitter's trace_id")
check(ev.span_id == sp.span_id, "normalized event in the pool carries the submitter's span_id")
check(ev.business_id == "biz-event", "normalized event inherits business_id through the pool")
check(ev.event_contributing_tokens == 15, "and its contributing total is intact")


# --- 7. Isolation: rebinding inside the worker never leaks back to the caller -------------
def opens_own_span() -> tuple[str, str] | None:
    with span():
        pass
    return ambient_ids()


with trace(business_id="biz-isolated"), span() as sp:
    with ContextPropagatingExecutor(max_workers=1) as pool:
        pool.submit(opens_own_span).result()
    after = ambient_ids()
check(
    after == (sp.trace_id, sp.span_id),
    "worker-side span rebinding is isolated: the caller's ambient context is untouched",
)

sys.exit(check.report("RESULT test_context_thread_pool_propagation"))
