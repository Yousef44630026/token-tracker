"""Phase 1 / step 4 — nested agent topology proof.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_context_propagation_nested_agent.py

A realistic agentic shape under ONE root trace:
    root
      |- llm (planning)
      |- tool call            (a span, then back to root)
      |- sub-agent            (its own span)
      |     |- sub llm        (child of the sub-agent, NOT of root)
      |- llm with a FAILED retry then a successful retry (same span_id, new corr ids)

Asserts the parent chain is exact, that exiting each span pops back precisely, that a
failed retry (exception) still resets, and that every recorded unit shares the one trace.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.context.propagation import current, retry, span, trace  # noqa: E402

_failures = 0
_events: list[dict] = []


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def record(label: str) -> None:
    """Simulate a token event attaching to whatever context is active right now."""
    c = current()
    _events.append(
        {
            "label": label,
            "trace_id": c.trace_id,
            "span_id": c.span_id,
            "parent_span_id": c.parent_span_id,
            "corr": c.request_correlation_id,
        }
    )


def by(label: str) -> dict:
    return next(e for e in _events if e["label"] == label)


def main() -> int:
    with trace(workflow="agent-run", environment="dev") as root:
        # planning LLM
        with span():
            record("plan_llm")
        check(current() is root, "after planning span, back at root")

        # tool call
        with span():
            record("tool_call")
        check(current() is root, "after tool span, back at root")

        # sub-agent with its own nested LLM
        with span() as sub_agent:
            record("sub_agent")
            with span():
                record("sub_llm")
            check(current() is sub_agent, "after sub_llm, back at sub_agent (not root)")
        check(current() is root, "after sub_agent, back at root")

        # an LLM span that fails once (retry) then succeeds
        with span() as call:
            corr_attempts = []
            # attempt 1 -> failure
            try:
                with retry() as a1:
                    corr_attempts.append(a1.request_correlation_id)
                    record("attempt_1")
                    raise TimeoutError("provider 5xx")
            except TimeoutError:
                pass
            check(current() is call, "failed retry attempt reset back to the call span")
            # attempt 2 -> success
            with retry() as a2:
                corr_attempts.append(a2.request_correlation_id)
                record("attempt_2")
            check(current() is call, "after successful retry, back at the call span")
            check(
                len(set(corr_attempts)) == 2,
                "two attempts -> two distinct request_correlation_ids",
            )
        check(current() is root, "after the call span, back at root")

    check(current() is None, "trace closed -> no active context")

    # --- topology assertions on recorded events ---
    root_id = by("plan_llm")["parent_span_id"]
    check(
        by("plan_llm")["parent_span_id"] == by("tool_call")["parent_span_id"] == by("sub_agent")["parent_span_id"] == root_id,
        "plan/tool/sub_agent all parent -> the single root span",
    )
    check(
        by("sub_llm")["parent_span_id"] == by("sub_agent")["span_id"],
        "sub_llm parent -> sub_agent (nested), NOT root",
    )
    check(
        by("sub_llm")["parent_span_id"] != root_id,
        "sub_llm is not mis-attributed to the root",
    )
    check(
        by("attempt_1")["span_id"] == by("attempt_2")["span_id"],
        "both retry attempts share ONE span_id (INV-5)",
    )
    check(
        by("attempt_1")["corr"] != by("attempt_2")["corr"],
        "retry attempts differ by request_correlation_id (INV-5)",
    )
    trace_ids = {e["trace_id"] for e in _events}
    check(len(trace_ids) == 1, "every recorded unit shares the one trace_id")

    print()
    if _failures:
        print(f"RESULT: {_failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
