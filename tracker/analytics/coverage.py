"""Coverage + exactness rollup for the CoverageExactness sheet. (Phase 9)

All values are DERIVED from the events (nothing stored). The headline number,
``observed_total_contributing_tokens``, is the same one derive/trace_rollup computes — so
the exported sheet can never disagree with the model. The rest are honest quality counts:
how much usage was exactly measured vs estimated vs lost (unknown), how many events carried
a provider total, and how many showed a provider/derived mismatch.

``exactness_ratio`` is computed over ALL quantities (exact + estimate + unknown), never just
the known ones — a denominator of only exact+estimate would let a trace with 90% UNKNOWN
quantities still report "100% exact" as long as the tiny known slice was all exact, which is
precisely the confident-zero-in-disguise INV-6 forbids at the token layer. ``known_exactness_ratio``
is kept as a narrower, explicitly-labeled second lens ("of what we actually measured, how much
was exact") for anyone who wants that specific question answered, but it is never the headline.
"""

from __future__ import annotations

from typing import Any

from tracker.derive.trace_rollup import observed_total_contributing_tokens
from tracker.models.enums import PrecisionLevel
from tracker.models.trace import Trace


def _ratio(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def build_coverage_exactness(trace: Trace) -> dict[str, Any]:
    """Return the ordered CoverageExactness metrics for a trace."""
    events = trace.events
    quantities = [q for e in events for q in e.quantities]

    exact = sum(1 for q in quantities if q.precision_level == PrecisionLevel.EXACT)
    estimate = sum(1 for q in quantities if q.precision_level == PrecisionLevel.ESTIMATE)
    unknown = sum(1 for q in quantities if q.precision_level == PrecisionLevel.UNKNOWN)
    known = exact + estimate
    events_with_total = sum(1 for e in events if e.provider_total_tokens is not None)
    mismatches = sum(1 for e in events if e.event_total_mismatch not in (None, 0))

    return {
        "observed_total_contributing_tokens": observed_total_contributing_tokens(trace),
        "event_count": len(events),
        "superseded_event_count": sum(1 for e in events if e.superseded),
        "quantity_count": len(quantities),
        "exact_quantity_count": exact,
        "estimate_quantity_count": estimate,
        "unknown_quantity_count": unknown,
        "provider_total_mismatch_count": mismatches,
        "events_with_provider_total": events_with_total,
        "coverage_ratio": _ratio(events_with_total, len(events)),
        # exact / EVERYTHING (including unknown) — the honest headline (see module docstring).
        "exactness_ratio": _ratio(exact, len(quantities)),
        # exact / (exact + estimate) — a narrower, explicitly-labeled second lens; never the
        # headline, because excluding unknown from its own denominator is what made the old
        # "exactness_ratio" able to read 100% while most of the data was actually missing.
        "known_exactness_ratio": _ratio(exact, known),
    }
