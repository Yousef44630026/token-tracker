"""Unknown-reason classifier (INV-6). (Phase 6)

When a quantity is None/unknown, WHY it is unknown is recorded as an UnknownReason so the
loss is surfaced as a typed count, never silently treated as zero. The cause is reported by
the layer that hit it (the stream tracker, the usage extractor, the normalizer) as boolean
signals; this classifier maps them to the single enum value, by precedence.

Precedence (most fundamental first): a normalization error means the adapter could not even
read usage; a missing raw-usage object is next; then stream timeout, stream interruption,
and finally a single provider-omitted field. Returns None when no cause is present — i.e.
the quantity is actually known.
"""

from __future__ import annotations

from tracker.models.enums import UnknownReason


def classify_unknown_reason(
    *,
    normalization_error: bool = False,
    raw_usage_missing: bool = False,
    timed_out: bool = False,
    interrupted: bool = False,
    provider_omitted: bool = False,
) -> UnknownReason | None:
    """Map cause signals to one UnknownReason (by precedence), or None if the value is known."""
    if normalization_error:
        return UnknownReason.NORMALIZATION_ERROR
    if raw_usage_missing:
        return UnknownReason.RAW_USAGE_MISSING
    if timed_out:
        return UnknownReason.STREAM_TIMEOUT
    if interrupted:
        return UnknownReason.STREAM_INTERRUPTED
    if provider_omitted:
        return UnknownReason.PROVIDER_OMITTED
    return None
