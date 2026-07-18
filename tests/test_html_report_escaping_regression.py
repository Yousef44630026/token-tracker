"""Regression — HTML report must escape `status` before interpolating it into an attribute.

Run: python tests/test_html_report_escaping_regression.py

Found during a rigorous review of tracker/export/html_report.py: `_rows_table()` built the
`<tr class="status-...">` attribute directly from `row.get("status")` without html.escape(),
unlike every other value interpolation in the same file. Not exploitable via today's call
sites (they only ever pass "pass"/"warn"/"fail"/None), but `status` can trace back to
event.observation, which any proxy/collector may populate from an external provider response —
so this proves the fix holds even for an adversarial value.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.export.html_report import _rows_table  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


adversarial_status = '"><script>alert(1)</script>'
html_out = _rows_table("Adversarial", [{"status": adversarial_status, "event_id": "e1"}])

check("<script>alert(1)</script>" not in html_out, "raw <script> tag does not survive unescaped into the rendered HTML")
check('class="status-&quot;&gt;' in html_out or "&quot;" in html_out, "the quote/angle-bracket characters are HTML-escaped")
check(
    '<tr class="status-"><script>' not in html_out,
    "the adversarial value cannot break out of the class attribute to inject a raw <tr ...> boundary",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
