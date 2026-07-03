"""Extra — robustness: edge values and empty containers.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_robustness_values.py

Zero / huge / negative token counts must not crash; empty traces roll up and export cleanly.
Note: negative token counts are REJECTED as invalid (TokenQuantity raises -> the normalizer
turns it into a normalization_error event, contributing 0); zero and huge values pass through.
"""

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens, roll_up  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


adapter = OpenAIChatCompletionsAdapter()


def usage(p, c, t):
    return {"usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": t}}


# --- zero everywhere: clean, contributes 0 ---
z = normalize(usage(0, 0, 0), adapter, context=new_trace())
check(z.event_contributing_tokens == 0 and z.event_total_mismatch == 0, "all-zero usage: contributes 0, no mismatch")
check(z.data_quality_flags == [], "all-zero usage: no flags")

# --- huge values: arbitrary-precision ints, no overflow ---
big = normalize(usage(10**12, 10**11, 10**12 + 10**11), adapter, context=new_trace())
check(big.event_contributing_tokens == 10**12 + 10**11, "huge values handled (no overflow)")
check(big.event_total_mismatch == 0, "huge values reconcile")

# --- negative value: rejected as invalid data, surfaced (not crashed) ---
neg = normalize(usage(-5, 10, 5), adapter, context=new_trace())
check(neg.event_contributing_tokens == 0, "negative input is not trusted -> contributes 0")
check("normalization_error" in neg.data_quality_flags, "negative input rejected -> normalization_error (no crash)")
mism = normalize(usage(100, 20, 999), adapter, context=new_trace())
check("provider_total_mismatch" in mism.data_quality_flags, "mismatch detector fires when total disagrees")

# --- empty trace: rolls up to 0, coverage clean, export produces header-only files ---
empty = Trace(trace_id="empty")
check(observed_total_contributing_tokens(empty) == 0, "empty trace total == 0")
r = roll_up(empty)
check(r.event_count == 0 and r.observed_total_contributing_tokens == 0, "empty rollup: 0 events / 0 total")
cov = build_coverage_exactness(empty)
check(cov["coverage_ratio"] == 0.0 and cov["exactness_ratio"] == 0.0, "empty coverage ratios == 0 (no divide-by-zero)")

out_dir = tempfile.mkdtemp(prefix="tt_empty_")
paths = export_csv(empty, out_dir)
check(
    {"token_quantities", "token_events", "token_spans"} <= set(paths),
    "export writes the 3 core CSV files (plus derived analytics summaries)",
)
with open(paths["token_events"], newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
check(rows == [], "empty trace -> header-only events CSV (0 data rows)")

# --- a trace of only superseded events totals 0 ---
sup = Trace(trace_id="sup")
sup.add_event(
    TokenEvent(
        event_id="s1",
        request_correlation_id="r",
        trace_id="sup",
        span_id="s",
        quantities=[
            TokenQuantity(TokenType.OUTPUT, 500, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)
        ],
        superseded=True,
        superseded_by="final",
    )
)
check(observed_total_contributing_tokens(sup) == 0, "all-superseded trace totals 0")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
