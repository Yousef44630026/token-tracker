"""Focused semantics, resilience, and responsive-shell checks for the HTML report."""

from __future__ import annotations

import os
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.export.html_report import _rows_table, render_html_report  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


class StructureAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_attrs = {}
        self.metas = []
        self.tables = 0
        self.captions = 0
        self.th_attrs = []
        self.table_regions = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.html_attrs = attributes
        elif tag == "meta":
            self.metas.append(attributes)
        elif tag == "table":
            self.tables += 1
        elif tag == "caption":
            self.captions += 1
        elif tag == "th":
            self.th_attrs.append(attributes)
        elif tag == "div" and attributes.get("class") == "table-wrap":
            if attributes.get("role") == "region" and attributes.get("tabindex") == "0":
                self.table_regions += 1


mixed_rows = _rows_table(
    "Mixed Rows",
    [
        {"name": "first", "status": "pass"},
        {"name": "second", "detail": "only present later"},
    ],
)
check('<th scope="col">detail</th>' in mixed_rows, "headers are the ordered union of all row keys")
check("only present later" in mixed_rows, "later-only values are not silently dropped")
check('class="badge unknown"' in mixed_rows, "missing status renders safely as an unknown badge")

markup = render_html_report(Trace(trace_id="ui-regression"), title="UI Regression Report")
audit = StructureAudit()
audit.feed(markup)

check(audit.html_attrs.get("lang") == "en", "document declares its language")
check(
    any(meta.get("name") == "viewport" and "width=device-width" in meta.get("content", "") for meta in audit.metas),
    "document includes a responsive viewport",
)
check(audit.tables > 0 and audit.captions == audit.tables, "every report table has a caption")
check(audit.th_attrs and all(attrs.get("scope") in {"row", "col"} for attrs in audit.th_attrs), "every table header declares scope")
check(audit.table_regions == audit.tables, "every table has a labeled keyboard-scrollable region")
check('class="skip-link"' in markup and 'id="report-content"' in markup, "report includes a skip link and main target")
check('class="verdict status-' in markup, "provider readiness is presented as a prominent verdict")
check("This status does not describe the health of the selected trace." in markup, "verdict explicitly qualifies its semantics")
check('<th scope="row">overall_status</th><td><span class="badge ' in markup, "overall status is rendered as a badge")
check("@media (max-width:700px)" in markup and "@media print" in markup, "responsive and print styles are embedded")
check(":focus-visible" in markup, "keyboard focus styling is embedded")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
