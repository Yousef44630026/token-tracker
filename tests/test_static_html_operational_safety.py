"""Static regression checks for the presentation and dashboard concept HTML."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESENTATION = (ROOT / "ARCHITECTURE_PRESENTATION.html").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "POWERBI_DASHBOARD_DESIGN.html").read_text(encoding="utf-8")

_failures = 0


def check(condition: bool, message: str) -> None:
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


check(
    re.search(
        r'id="slideAnnouncement"[^>]*aria-live="polite"[^>]*aria-atomic="true"',
        PRESENTATION,
    )
    is not None,
    "slide changes use an atomic polite live region",
)
check(
    "isInteractiveOrEditable(event.target)" in PRESENTATION,
    "global slide shortcuts guard interactive and editable event targets",
)
for protected_target in (
    '"button"',
    '"input"',
    '"select"',
    '"textarea"',
    '"[contenteditable]:not([contenteditable=\'false\'])"',
):
    check(
        protected_target in PRESENTATION,
        f"shortcut guard covers {protected_target}",
    )
check(
    "event.defaultPrevented ||" in PRESENTATION
    and "event.ctrlKey ||" in PRESENTATION
    and "event.metaKey ||" in PRESENTATION,
    "global shortcuts preserve handled and modified keystrokes",
)
check(
    "focusTarget.focus({ preventScroll: true })" in PRESENTATION
    and "if (options.focusHeading)" in PRESENTATION,
    "deliberate slide navigation focuses the active heading without scrolling",
)
check(
    "slideAnnouncement.textContent = `Slide ${index + 1} of ${slides.length}: ${announcementLabel}`"
    in PRESENTATION,
    "the live announcement identifies the slide position and heading",
)

notice_match = re.search(
    r'<div class="prototype-notice"[^>]*role="note"[^>]*>(.*?)</div>',
    DASHBOARD,
    flags=re.DOTALL,
)
notice_text = re.sub(r"<[^>]+>", " ", notice_match.group(1)).lower() if notice_match else ""
check(
    notice_match is not None
    and "all figures" in notice_text
    and "sample data" in notice_text
    and "no live source" in notice_text,
    "the dashboard persistently labels every figure as illustrative and non-live",
)
check(
    'aria-describedby="prototypeNotice"' in DASHBOARD,
    "the dashboard content is programmatically associated with the prototype notice",
)

filters_match = re.search(
    r'<div class="filters"(?P<attrs>[^>]*)>(?P<body>.*?)</div>\s*</section>',
    DASHBOARD,
    flags=re.DOTALL,
)
filter_attrs = filters_match.group("attrs") if filters_match else ""
filter_body = filters_match.group("body") if filters_match else ""
filter_selects = re.findall(r"<select\b[^>]*>", filter_body)
check(
    filters_match is not None
    and 'aria-disabled="true"' in filter_attrs
    and "filters are disabled" in filter_body.lower(),
    "the filter area unmistakably states that it is non-operational",
)
check(
    len(filter_selects) == 4 and all(re.search(r"\bdisabled\b", tag) for tag in filter_selects),
    "every illustrative dashboard filter is natively disabled",
)
for control_id in ("filterDateRange", "filterEnvironment", "filterProvider", "filterService"):
    check(
        f'<label for="{control_id}">' in filter_body
        and re.search(rf'<select\b[^>]*\bid="{control_id}"[^>]*\bdisabled\b', filter_body)
        is not None,
        f"disabled filter {control_id} retains an associated label",
    )
check(
    ".filter select:disabled" in DASHBOARD and "cursor: not-allowed" in DASHBOARD,
    "disabled filters retain a clear visual affordance",
)

if _failures:
    raise SystemExit(f"{_failures} static HTML regression check(s) failed")

print("Static presentation and dashboard operational-safety regression checks passed.")
