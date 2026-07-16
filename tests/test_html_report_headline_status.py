"""Regression — the HTML report's headline number must carry its epistemic status.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_html_report_headline_status.py

The HTML report is the layer humans actually read, and its "Trace Summary" section is where the
eye lands first: it shows ``observed_total_contributing_tokens`` prominently. But that number is
a POINT value only when every real token was both measured and counted; otherwise it is a FLOOR
(the true total is >= it). Before this fix, the Trace Summary showed the total naked — the
``total_is_lower_bound`` flag and the floor/estimate/ceiling band lived only in a separate
"Coverage And Exactness" section further down. A reader scanning the headline saw a confident
number with no hint it was a lower bound, which is exactly the "present a floor as a measurement"
sin the whole lower-bound doctrine (test_lower_bound_signal_regression) exists to prevent.

This pins that the Trace Summary section itself carries the band + the lower-bound flag, and that
those values RECONCILE with the trace rollup (the source of truth), never recomputed.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.trace_rollup import roll_up  # noqa: E402
from tracker.export.html_report import render_html_report  # noqa: E402
from tracker.models.enums import (
    Additivity,
    PrecisionLevel,
    TokenType,
    UsageSource,
)  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(tt, qty, prec, src, add=Additivity.TOTAL_CONTRIBUTING):
    return TokenQuantity(tt, qty, prec, src, add)


def trace_summary_section(html: str) -> str:
    """Return only the Trace Summary <section> markup (not the whole document)."""
    heading = re.search(r"<h2[^>]*>Trace Summary</h2>", html)
    assert heading is not None, "Trace Summary heading present"
    start = heading.start()
    end = html.find("</section>", start)
    return html[start:end]


def cell(section: str, key: str) -> str | None:
    """Return the rendered value for a metric row ``key`` inside a section, or None."""
    match = re.search(rf"<tr><th[^>]*>{re.escape(key)}</th><td>(.*?)</td></tr>", section)
    return match.group(1) if match else None


# --- lower-bound trace: input 100 counted + an exact-but-UNVERIFIED cached 900 (contributes 0).
# observed = 100; unattributed = 0; estimate = 100; floor = 100;
# unverified_independent = 900 -> ceiling = 1000; capture = 100/1000 = 0.1; lower_bound = True.
lb = Trace(trace_id="lb")
lb.add_event(
    TokenEvent(
        event_id="e1",
        request_correlation_id="r1",
        trace_id="lb",
        span_id="s",
        quantities=[
            q(
                TokenType.INPUT,
                100,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
            ),
            q(
                TokenType.CACHED_INPUT,
                900,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.UNVERIFIED,
            ),
        ],
        observation={"authoritative": True},
    )
)
lb_rollup = roll_up(lb)
lb_section = trace_summary_section(render_html_report(lb))

check(
    cell(lb_section, "observed_total_contributing_tokens") == "100",
    "Trace Summary still shows the observed total (100)",
)
check(
    cell(lb_section, "total_is_lower_bound") == "True",
    "Trace Summary carries total_is_lower_bound=True next to the headline",
)
check(
    cell(lb_section, "headline_floor_tokens") == "100",
    "Trace Summary carries headline_floor_tokens=100",
)
check(
    cell(lb_section, "headline_estimate_tokens") == "100",
    "Trace Summary carries headline_estimate_tokens=100",
)
check(
    cell(lb_section, "headline_ceiling_tokens") == "1000",
    "Trace Summary carries headline_ceiling_tokens=1000 (900 unverified-independent)",
)
check(
    cell(lb_section, "capture_completeness_ratio") == "0.1",
    "Trace Summary carries capture_completeness_ratio=0.1",
)

# reconciliation: the section's values are the rollup's, never recomputed
check(
    cell(lb_section, "total_is_lower_bound") == str(lb_rollup.total_is_lower_bound),
    "lower_bound reconciles with rollup",
)
check(
    cell(lb_section, "headline_floor_tokens") == str(lb_rollup.headline_floor_tokens),
    "floor reconciles with rollup",
)
check(
    cell(lb_section, "headline_estimate_tokens") == str(lb_rollup.headline_estimate_tokens),
    "estimate reconciles with rollup",
)
check(
    cell(lb_section, "headline_ceiling_tokens") == str(lb_rollup.headline_ceiling_tokens),
    "ceiling reconciles with rollup",
)

# --- clean trace: everything counted + exact, provider total reconciles -> a POINT value.
clean = Trace(trace_id="clean")
clean.add_event(
    TokenEvent(
        event_id="c1",
        request_correlation_id="rc",
        trace_id="clean",
        span_id="s",
        quantities=[
            q(
                TokenType.INPUT,
                100,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
            ),
            q(
                TokenType.OUTPUT,
                50,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
            ),
        ],
        provider_total_tokens=150,
        observation={"authoritative": True},
    )
)
clean_section = trace_summary_section(render_html_report(clean))
check(
    cell(clean_section, "observed_total_contributing_tokens") == "150",
    "clean Trace Summary shows the observed total (150)",
)
check(
    cell(clean_section, "total_is_lower_bound") == "False",
    "clean Trace Summary marks the total exact (not a lower bound)",
)
check(
    cell(clean_section, "headline_floor_tokens") == "150",
    "clean floor == estimate == ceiling == 150",
)
check(
    cell(clean_section, "headline_ceiling_tokens") == "150",
    "clean ceiling == 150 (no unverified/unknown to widen the band)",
)
check(
    cell(clean_section, "capture_completeness_ratio") == "1.0",
    "clean capture_completeness_ratio == 1.0",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
