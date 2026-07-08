"""Data-quality flags — normalizer-produced subset (single producer each). (Phase 10)

Each data-quality flag has exactly ONE producer. These three are produced by the NORMALIZER
(not the adapter, not the stream tracker), computed from the normalized quantities + the raw
provider total:

  - unverified_additivity   : any quantity has additivity == "unverified"
  - unknown_quantity_present: any quantity is unknown (None / precision unknown)
  - provider_total_mismatch : provider_total_tokens != sum(quantity_in_total)

Other flags (partial_stream_estimate, stream_interrupted, superseded, propagation_lost,
raw_usage_missing, normalization_error) are raised by their own single producers elsewhere.
"""

from __future__ import annotations

from tracker.models.enums import DataQualityFlag, PrecisionLevel, Trust
from tracker.models.token_quantity import TokenQuantity

UNVERIFIED_ADDITIVITY = DataQualityFlag.UNVERIFIED_ADDITIVITY.value
UNKNOWN_QUANTITY_PRESENT = DataQualityFlag.UNKNOWN_QUANTITY_PRESENT.value
PROVIDER_TOTAL_MISMATCH = DataQualityFlag.PROVIDER_TOTAL_MISMATCH.value
PROVIDER_TOTAL_UNDER_ATTRIBUTION = DataQualityFlag.PROVIDER_TOTAL_UNDER_ATTRIBUTION.value
PROVIDER_TOTAL_OVER_ATTRIBUTION = DataQualityFlag.PROVIDER_TOTAL_OVER_ATTRIBUTION.value


def normalizer_flags(quantities: list[TokenQuantity], provider_total_tokens: int | None) -> list[str]:
    """Return the normalizer-produced data-quality flags for one event's quantities."""
    flags: list[str] = []
    if any(q.trust == Trust.UNVERIFIED for q in quantities):
        flags.append(UNVERIFIED_ADDITIVITY)
    if any(q.quantity is None or q.precision_level == PrecisionLevel.UNKNOWN for q in quantities):
        flags.append(UNKNOWN_QUANTITY_PRESENT)
    if provider_total_tokens is not None:
        contributing = sum(q.quantity_in_total for q in quantities)
        mismatch = provider_total_tokens - contributing
        if mismatch != 0:
            flags.append(PROVIDER_TOTAL_MISMATCH)
            if mismatch > 0:
                flags.append(PROVIDER_TOTAL_UNDER_ATTRIBUTION)
            else:
                flags.append(PROVIDER_TOTAL_OVER_ATTRIBUTION)
    return flags
