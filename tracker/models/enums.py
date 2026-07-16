"""Enums for the token model. (Phase 2)

All enums subclass ``str`` so a member compares equal to its wire string
(``Additivity.TOTAL_CONTRIBUTING == "total_contributing"``). This lets the INV-2
derivations be written against the spec strings and keeps JSONL serialization trivial
(the value is already a plain string).
"""

from __future__ import annotations

from enum import Enum


class TokenType(str, Enum):
    """WHAT the tokens are — never how well they were measured (INV-3).

    Forbidden-by-construction: partial_output_observed / estimated_input /
    estimated_output cannot exist as a member, so INV-3 is enforced at the type level.
    ``total`` is NOT a token type (provider total is event-level raw data).
    """

    INPUT = "input"
    OUTPUT = "output"
    CACHED_INPUT = "cached_input"
    CACHE_CREATION_INPUT = "cache_creation_input"
    REASONING = "reasoning"
    THINKING = "thinking"
    EMBEDDING = "embedding"
    RERANK_INPUT = "rerank_input"
    RERANK_OUTPUT = "rerank_output"
    AUDIO_INPUT = "audio_input"
    AUDIO_OUTPUT = "audio_output"
    IMAGE_INPUT = "image_input"
    VIDEO_INPUT = "video_input"


class PrecisionLevel(str, Enum):
    """How well a quantity was measured — orthogonal to token_type (INV-3)."""

    EXACT = "exact"
    ESTIMATE = "estimate"
    UNKNOWN = "unknown"


class UsageSource(str, Enum):
    """Where a quantity came from."""

    PROVIDER_RESPONSE = "provider_response"
    PROVIDER_STREAM_FINAL = "provider_stream_final"
    # The provider's own cumulative usage from a MID-stream event: an exact count of what was
    # produced so far, but a floor of the final output when the stream is then interrupted.
    PROVIDER_STREAM_PARTIAL = "provider_stream_partial"
    PARTIAL_STREAM_TOKENIZER = "partial_stream_tokenizer"
    LOCAL_TOKENIZER = "local_tokenizer"
    HISTORICAL_FORECAST = "historical_forecast"
    NONE = "none"


class UnknownReason(str, Enum):
    """Why a quantity is unknown (quantity is None). Used only with PrecisionLevel.UNKNOWN."""

    STREAM_TIMEOUT = "stream_timeout"
    STREAM_INTERRUPTED = "stream_interrupted"
    RAW_USAGE_MISSING = "raw_usage_missing"
    PROVIDER_OMITTED = "provider_omitted"
    NORMALIZATION_ERROR = "normalization_error"


class Additivity(str, Enum):
    """Adapter-assigned, never inferred from the type string (INV-4).

    This legacy-compatible field is kept for the three original categories, while
    ``TokenQuantity.overlap`` / ``TokenQuantity.trust`` store the two orthogonal axes that a
    token count raises:

        TOTAL_CONTRIBUTING == (Overlap.INDEPENDENT, Trust.VERIFIED)
        SUBTOTAL_OF        == (Overlap.SUBTOTAL_OF, Trust.VERIFIED)
        UNVERIFIED         == Trust.UNVERIFIED with either overlap value

    Only an (independent, verified) quantity is summed; the two axes make explicit that a count
    is excluded either because it is a breakdown of another (overlap) or because its additivity
    is not yet trusted (trust) — two different reasons the flat enum used to conflate.
    """

    TOTAL_CONTRIBUTING = "total_contributing"
    SUBTOTAL_OF = "subtotal_of"
    UNVERIFIED = "unverified"


class Overlap(str, Enum):
    """STRUCTURAL axis: is this count already contained within another count?

    INDEPENDENT  — stands on its own; eligible to be summed into the total.
    SUBTOTAL_OF  — a breakdown already inside a parent count (e.g. cached_input inside input);
                   never summed, or it would double-count the parent.
    """

    INDEPENDENT = "independent"
    SUBTOTAL_OF = "subtotal_of"


class Trust(str, Enum):
    """VERIFICATION axis: do we trust this count's additivity enough to sum it?

    VERIFIED    — the adapter has confirmed how this count relates to the total.
    UNVERIFIED  — additivity not yet confirmed against a real payload (fail closed: contribute
                  0 and flag), or an unfamiliar field that fell through to the safe default.
    Orthogonal to ``Overlap`` and to ``PrecisionLevel`` (which is about measurement quality,
    not additivity trust).
    """

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class AggregationMode(str, Enum):
    """How quantities of the same kind aggregate. MVP uses SUM only.

    MAX/LAST are reserved for future use and MUST NOT be relied on yet.
    """

    SUM = "sum"
    MAX = "max"  # reserved, unused in MVP
    LAST = "last"  # reserved, unused in MVP


class DataQualityFlag(str, Enum):
    """Registered data-quality labels.

    Events store flags as strings for JSONL stability. New flags should be registered here
    first; unknown caller-supplied labels are capped to ``custom`` before storage so analytics
    cardinality stays bounded.
    """

    PROVIDER_TOTAL_MISMATCH = "provider_total_mismatch"
    PROVIDER_TOTAL_UNDER_ATTRIBUTION = "provider_total_under_attribution"
    PROVIDER_TOTAL_OVER_ATTRIBUTION = "provider_total_over_attribution"
    AUTHORITY_MISSING = "authority_missing"
    UNVERIFIED_ADDITIVITY = "unverified_additivity"
    UNKNOWN_QUANTITY_PRESENT = "unknown_quantity_present"
    PARTIAL_STREAM_ESTIMATE = "partial_stream_estimate"
    STREAM_INTERRUPTED = "stream_interrupted"
    SUPERSEDED = "superseded"
    CORRELATION_ID_COLLISION = "correlation_id_collision"
    PROPAGATION_LOST = "propagation_lost"
    RAW_USAGE_MISSING = "raw_usage_missing"
    PROVIDER_SCHEMA_DRIFT = "provider_schema_drift"
    NORMALIZATION_ERROR = "normalization_error"
    INPUT_ESTIMATE_ONLY = "input_estimate_only"
    PROVIDER_USAGE_MISSING = "provider_usage_missing"
    PROVIDER_STREAM_USAGE_MISSING = "provider_stream_usage_missing"
    PROVIDER_RESPONSE_UNPARSEABLE = "provider_response_unparseable"
    PROVIDER_HTTP_ERROR = "provider_http_error"
    PROXY_UPSTREAM_ERROR = "proxy_upstream_error"
    AUTH_FAILURE = "auth_failure"
    DEPLOYMENT_OR_ENDPOINT_NOT_FOUND = "deployment_or_endpoint_not_found"
    TIMEOUT = "timeout"
    RATE_LIMITED_OR_QUOTA = "rate_limited_or_quota"
    CONTENT_FILTER = "content_filter"
    DNS_FAILURE = "dns_failure"
    NETWORK_OR_CLIENT_FAILURE = "network_or_client_failure"
    CLAUDE_CODE_LOCAL_USAGE = "claude_code_local_usage"
    CODEX_LOCAL_TOKEN_COUNT = "codex_local_token_count"
    CUSTOM = "custom"
