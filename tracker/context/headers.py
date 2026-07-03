"""En-tetes inter-services X-TokenTracker-*. (Phase 1)

Step 2 of Phase 1: serialize a ``TraceContext`` to HTTP headers and rehydrate it on
the other side of a process/service boundary, where ambient context cannot travel
implicitly.

The required identity (trace_id, span_id, request_correlation_id) is always emitted;
optional labels (parent_span_id, business_id, workflow, environment) are emitted only
when present. ``extract`` is case-insensitive (HTTP header names are) and returns
``None`` when the required identity is incomplete — the propagation layer turns that
``None`` into a ``propagation_lost`` flag rather than silently re-rooting.
"""

from __future__ import annotations

from collections.abc import Mapping

from tracker.context.model import TraceContext

PREFIX = "X-TokenTracker-"

# header suffix  <->  TraceContext field
_FIELD_BY_HEADER = {
    "Trace-Id": "trace_id",
    "Span-Id": "span_id",
    "Request-Correlation-Id": "request_correlation_id",
    "Parent-Span-Id": "parent_span_id",
    "Business-Id": "business_id",
    "Workflow": "workflow",
    "Environment": "environment",
}
_REQUIRED_FIELDS = ("trace_id", "span_id", "request_correlation_id")


def inject(ctx: TraceContext) -> dict[str, str]:
    """Serialize a context to X-TokenTracker-* headers; omit None optional fields."""
    out: dict[str, str] = {}
    for suffix, field in _FIELD_BY_HEADER.items():
        value = getattr(ctx, field)
        if value is not None:
            out[PREFIX + suffix] = value
    return out


def extract(headers: Mapping[str, str]) -> TraceContext | None:
    """Rehydrate a context from headers, or None if the required identity is missing.

    Case-insensitive on header keys; unrelated headers are ignored.
    """
    # normalize incoming keys to lowercase for case-insensitive lookup
    lowered = {k.lower(): v for k, v in headers.items()}

    values: dict[str, str] = {}
    for suffix, field in _FIELD_BY_HEADER.items():
        key = (PREFIX + suffix).lower()
        if key in lowered:
            values[field] = lowered[key]

    # A required field must be present AND non-blank. The `.strip()` (not just truthiness) is
    # deliberate: TraceContext.__post_init__ rejects a whitespace-only id, so a header like
    # `X-TokenTracker-Trace-Id: "   "` would otherwise pass this guard (a space string is
    # truthy) and then RAISE inside the TraceContext constructor — breaking extract()'s
    # documented "return None on incomplete identity, never raise" contract. That raise, in the
    # proxy's _measurement(), aborts the real provider call (INV: observation must never break
    # the API call). Treat a blank required id as missing identity -> return None -> the
    # propagation layer flags propagation_lost, which is the intended degraded behavior.
    if any(field not in values or not values[field].strip() for field in _REQUIRED_FIELDS):
        return None

    return TraceContext(
        trace_id=values["trace_id"],
        span_id=values["span_id"],
        request_correlation_id=values["request_correlation_id"],
        parent_span_id=values.get("parent_span_id"),
        business_id=values.get("business_id"),
        workflow=values.get("workflow"),
        environment=values.get("environment"),
    )
