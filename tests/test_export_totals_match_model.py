"""Phase 9 — exported CSV totals must equal the model (the core falsifier).

Run: python tests/test_export_totals_match_model.py

Materialized export columns must agree with the in-memory model and with each other:

    SUM(quantity_in_total over NON-superseded events)
        == SUM(event_contributing_tokens)
        == model trace total (derive/trace_rollup)
        == CoverageExactness value

and summing the RAW quantity column must give a DIFFERENT (larger) number — proving the
export never sums raw quantity or provider_total, and never mixes event-grain with
quantity-grain in one sum.
"""

import csv
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.coverage import build_coverage_exactness  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.additivity import assign_additivity  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def q(tt, qty, src=UsageSource.PROVIDER_RESPONSE, precision=PrecisionLevel.EXACT):
    additivity, subtotal_of = assign_additivity("openai", "responses", tt)
    return TokenQuantity(
        token_type=tt,
        quantity=qty,
        precision_level=precision,
        usage_source=src,
        additivity=additivity,
        subtotal_of=subtotal_of,
    )


# Event A: input 1000 (cached 800 subtotal), output 300 (reasoning 250 subtotal) -> 1300
a = TokenEvent(
    event_id="evt-a",
    request_correlation_id="r-a",
    trace_id="t-1",
    span_id="s-1",
    provider="openai",
    api_surface="responses",
    quantities=[q(TokenType.INPUT, 1000), q(TokenType.OUTPUT, 300), q(TokenType.CACHED_INPUT, 800), q(TokenType.REASONING, 250)],
    provider_total_tokens=1300,
    observation={"authoritative": True},
)
# Event B: output 200 -> 200
b = TokenEvent(
    event_id="evt-b",
    request_correlation_id="r-b",
    trace_id="t-1",
    span_id="s-2",
    provider="openai",
    api_surface="responses",
    quantities=[q(TokenType.OUTPUT, 200)],
    provider_total_tokens=200,
    observation={"authoritative": True},
)
# Event C: a superseded partial estimate (output 40) -> contributes 0
c = TokenEvent(
    event_id="evt-c",
    request_correlation_id="r-b",
    trace_id="t-1",
    span_id="s-2",
    provider="openai",
    api_surface="responses",
    quantities=[q(TokenType.OUTPUT, 40, UsageSource.PARTIAL_STREAM_TOKENIZER, PrecisionLevel.ESTIMATE)],
    superseded=True,
    superseded_by="evt-b",
    data_quality_flags=["superseded"],
    observation={"authoritative": True},
)

trace = Trace(trace_id="t-1")
for e in (a, b, c):
    trace.add_event(e)

model_total = observed_total_contributing_tokens(trace)
check(model_total == 1500, f"model trace total == 1500 (got {model_total})")

out_dir = os.path.join(os.getcwd(), ".test_export_out")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir, exist_ok=True)
paths = export_csv(trace, out_dir)

# --- quantity grain: sum quantity_in_total over NON-superseded events ---
with open(paths["token_quantities"], newline="", encoding="utf-8") as f:
    qrows = list(csv.DictReader(f))
qgrain = sum(int(r["quantity_in_total"]) for r in qrows if r["event_superseded"] == "False")
raw_sum = sum(int(r["quantity"]) for r in qrows if r["quantity"])
check(qgrain == model_total, f"SUM(quantity_in_total, non-superseded) == model (got {qgrain})")
check(raw_sum != model_total, f"SUM(raw quantity) != model -> raw is never summed (raw={raw_sum})")

# --- event grain: sum event_contributing_tokens ---
with open(paths["token_events"], newline="", encoding="utf-8") as f:
    erows = list(csv.DictReader(f))
egrain = sum(int(r["event_contributing_tokens"]) for r in erows)
check(egrain == model_total, f"SUM(event_contributing_tokens) == model (got {egrain})")
superseded_row = next(r for r in erows if r["event_id"] == "evt-c")
check(superseded_row["event_contributing_tokens"] == "0", "superseded event row contributes 0")

# --- CoverageExactness value ---
cov = build_coverage_exactness(trace)
check(cov["observed_total_contributing_tokens"] == model_total, "CoverageExactness value == model")

check(qgrain == egrain == cov["observed_total_contributing_tokens"] == model_total, "all four totals agree")

shutil.rmtree(out_dir, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
