"""Phase 1 / step 2 — cross-service header inject/extract.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_context_headers.py

Serialize a TraceContext to X-TokenTracker-* headers and rehydrate it:
  - round-trip fidelity (inject -> extract == original) for root and child contexts
  - exactly the documented header names, with the X-TokenTracker- prefix
  - None optional fields are OMITTED (no empty Parent-Span-Id at the root)
  - extract is case-insensitive on header keys and ignores unrelated headers
  - missing required ids -> extract returns None (caller will flag propagation_lost)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.headers import extract, inject  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402

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

    h = inject(root)

    # exact header names
    expected_keys = {
        "X-TokenTracker-Trace-Id",
        "X-TokenTracker-Span-Id",
        "X-TokenTracker-Request-Correlation-Id",
        "X-TokenTracker-Business-Id",
        "X-TokenTracker-Workflow",
        "X-TokenTracker-Environment",
    }
    check(set(h.keys()) == expected_keys, "root injects exactly the expected header names")
    check(
        "X-TokenTracker-Parent-Span-Id" not in h,
        "None parent_span_id is OMITTED (no empty header at the root)",
    )
    check(h["X-TokenTracker-Trace-Id"] == root.trace_id, "trace id value injected verbatim")

    # round-trip root
    back = extract(h)
    check(back == root, "root round-trips inject -> extract unchanged")

    # round-trip a child (carries parent_span_id)
    child = root.child_span()
    hc = inject(child)
    check("X-TokenTracker-Parent-Span-Id" in hc, "child injects Parent-Span-Id")
    check(extract(hc) == child, "child round-trips inject -> extract unchanged")

    # case-insensitive keys + ignores unrelated headers
    lowered = {k.lower(): v for k, v in h.items()}
    lowered["content-type"] = "application/json"
    check(extract(lowered) == root, "extract is case-insensitive and ignores unrelated headers")

    # missing required ids -> None
    check(extract({}) is None, "empty headers -> extract returns None")
    partial = dict(h)
    del partial["X-TokenTracker-Span-Id"]
    check(extract(partial) is None, "missing Span-Id (required) -> extract returns None")

    # minimal context (no optional labels) still round-trips
    minimal = new_trace()
    check(extract(inject(minimal)) == minimal, "minimal context (no labels) round-trips")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
