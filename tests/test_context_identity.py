"""Phase 1 / step 1 — context identity model (pure data, no concurrency yet).

Run: python tests/test_context_identity.py

Asserts the trace/span/parent/request_correlation_id relationships that the whole
tracker hangs off:
  - a root context has no parent span and fresh ids
  - a child span keeps the trace, gets a new span, and points parent->current span
  - business/workflow/environment are INHERITED by child spans
  - a retry keeps the SAME span_id but mints a NEW request_correlation_id
    (INV-5: supersession correlates on request_correlation_id, not span_id)
  - all minted ids are unique and non-empty
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.propagation import TraceContext, new_trace  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def main() -> int:
    root = new_trace(business_id="biz-1", workflow="rag-eval", environment="prod")

    check(isinstance(root, TraceContext), "new_trace returns a TraceContext")
    check(bool(root.trace_id), "root has a non-empty trace_id")
    check(bool(root.span_id), "root has a non-empty span_id")
    check(root.parent_span_id is None, "root has no parent_span_id")
    check(bool(root.request_correlation_id), "root has a request_correlation_id")
    check(root.business_id == "biz-1", "root carries business_id")
    check(root.workflow == "rag-eval", "root carries workflow")
    check(root.environment == "prod", "root carries environment")

    # --- child span ---
    child = root.child_span()
    check(child.trace_id == root.trace_id, "child keeps the trace_id")
    check(child.span_id != root.span_id, "child gets a new span_id")
    check(child.parent_span_id == root.span_id, "child.parent_span_id == root.span_id")
    check(
        child.request_correlation_id != root.request_correlation_id,
        "child span is a new logical call -> new request_correlation_id",
    )
    check(child.business_id == "biz-1", "child inherits business_id")
    check(child.workflow == "rag-eval", "child inherits workflow")
    check(child.environment == "prod", "child inherits environment")

    # --- retry within the SAME span ---
    retry = child.retry()
    check(retry.span_id == child.span_id, "retry keeps the SAME span_id")
    check(retry.trace_id == child.trace_id, "retry keeps the trace_id")
    check(retry.parent_span_id == child.parent_span_id, "retry keeps the parent_span_id")
    check(
        retry.request_correlation_id != child.request_correlation_id,
        "retry mints a NEW request_correlation_id (INV-5 keys on this, not span_id)",
    )

    # --- uniqueness / immutability ---
    ids = {
        root.span_id,
        child.span_id,
        root.request_correlation_id,
        child.request_correlation_id,
        retry.request_correlation_id,
    }
    check(len(ids) == 5, "all minted span/correlation ids are unique")
    check(TraceContext.__dataclass_params__.frozen, "TraceContext is frozen/immutable")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
