"""Phase 1 / step 3 — propagation core (contextvars, span managers, header resume).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_context_core.py

Single-threaded semantics + strict set/reset discipline (real concurrency is step 4):
  - current() is None outside any context
  - trace()/span() bind the active context and RESTORE the previous one on exit
  - a child span points parent -> enclosing span; exiting pops back exactly
  - an exception inside a span still resets (finally discipline)
  - retry() keeps span_id, mints a new request_correlation_id
  - continue_from_headers: valid headers -> child of remote, not lost;
    partial tracker headers -> new root + propagation_lost; no tracker headers -> clean root
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context import headers as H  # noqa: E402
from tracker.context.propagation import (  # noqa: E402
    continue_from_headers,
    current,
    new_trace,
    retry,
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


def main() -> int:
    check(current() is None, "current() is None outside any context")

    with trace(business_id="biz-1", workflow="wf", environment="dev") as root:
        check(current() is root, "trace() binds the root as active")
        check(current().parent_span_id is None, "root active has no parent")

        with span() as child:
            check(current() is child, "span() binds the child as active")
            check(child.parent_span_id == root.span_id, "child.parent -> enclosing span")
            check(child.trace_id == root.trace_id, "child keeps the trace")

            with retry() as r:
                check(current() is r, "retry() binds the retried attempt")
                check(r.span_id == child.span_id, "retry keeps span_id")
                check(
                    r.request_correlation_id != child.request_correlation_id,
                    "retry mints a new request_correlation_id",
                )
            check(current() is child, "exiting retry() pops back to the child span")

        check(current() is root, "exiting span() pops back to the root")

        # exception inside a span must still reset
        try:
            with span():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        check(current() is root, "exception inside span() still resets to root (finally)")

    check(current() is None, "exiting trace() restores None")

    # --- continue_from_headers: valid remote context ---
    remote = new_trace(business_id="biz-x", workflow="wf2", environment="prod")
    hdrs = H.inject(remote)
    with continue_from_headers(hdrs) as res:
        check(res.propagation_lost is False, "valid headers -> not propagation_lost")
        check(res.context.trace_id == remote.trace_id, "resumes the remote trace_id")
        check(res.context.parent_span_id == remote.span_id, "opens a child of the remote span")
        check(current() is res.context, "continue_from_headers binds the resumed context")
    check(current() is None, "continue_from_headers resets on exit")

    # --- partial/corrupt tracker headers -> propagation lost ---
    partial = dict(hdrs)
    del partial["X-TokenTracker-Span-Id"]
    with continue_from_headers(partial) as res:
        check(res.propagation_lost is True, "partial tracker headers -> propagation_lost")
        check("propagation_lost" in res.flags, "flag 'propagation_lost' is surfaced")
        check(res.context.parent_span_id is None, "lost propagation starts a fresh root")

    # --- no tracker headers at all -> clean fresh root, NOT lost ---
    with continue_from_headers({"content-type": "application/json"}) as res:
        check(res.propagation_lost is False, "no tracker headers -> clean root, not lost")
        check(res.flags == (), "clean root has no flags")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
