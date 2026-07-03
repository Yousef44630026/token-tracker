"""Derived cache-efficiency metrics."""

from __future__ import annotations

from typing import Any

from tracker.analytics._common import authoritative_events, quantity_sum, ratio
from tracker.models.enums import Additivity, TokenType
from tracker.models.trace import Trace


def build_cache_summary(trace: Trace) -> dict[str, Any]:
    """Return prompt-cache metrics for a trace.

    Cache tokens are visibility metrics even when they are subtotals. The denominator uses
    authoritative prompt/input buckets so OpenAI-style subtotal cache and Anthropic-style
    additive cache both remain comparable.

    ``fresh_input_tokens`` = total prompt cost minus verified cache read/creation, computed
    the SAME way regardless of whether a provider reports cache as a subtotal-of-input
    (OpenAI: raw input_tokens already includes the cached portion) or as a separate additive
    bucket (Anthropic: input_tokens already excludes it). Deriving it from
    ``prompt_input_tokens`` (which already handles both additivity styles correctly via
    ``quantity_in_total``) instead of reading TokenType.INPUT directly avoids the previous bug:
    a naive "just take the INPUT quantity" reading returned the FULL cache-inclusive input for
    OpenAI-style events (since cached is a subtotal that never reduces INPUT's own value) while
    genuinely meaning "fresh" for Anthropic-style events — the same field name silently meant
    two different things depending on which provider produced the event.
    """
    events = authoritative_events(trace)
    # NOTE: quantity_sum(..., include_unverified=False) is the DEFAULT, so these ARE the
    # verified-only totals already — kept under both names (cache_read_tokens /
    # verified_cache_read_tokens) because both are referenced elsewhere in this file/export,
    # not recomputed twice.
    cache_read_tokens = quantity_sum(events, TokenType.CACHED_INPUT)
    cache_creation_tokens = quantity_sum(events, TokenType.CACHE_CREATION_INPUT)
    verified_cache_read_tokens = cache_read_tokens
    verified_cache_creation_tokens = cache_creation_tokens
    unverified_cache_read_tokens = sum(
        quantity.quantity or 0
        for event in events
        for quantity in event.quantities
        if quantity.token_type == TokenType.CACHED_INPUT and quantity.additivity == Additivity.UNVERIFIED and quantity.quantity is not None
    )
    unverified_cache_creation_tokens = sum(
        quantity.quantity or 0
        for event in events
        for quantity in event.quantities
        if quantity.token_type == TokenType.CACHE_CREATION_INPUT
        and quantity.additivity == Additivity.UNVERIFIED
        and quantity.quantity is not None
    )
    prompt_input_tokens = sum(
        quantity.quantity_in_total
        for event in events
        for quantity in event.quantities
        if quantity.token_type in (TokenType.INPUT, TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT)
    )
    # See the docstring: derived from prompt_input_tokens, not read directly off INPUT, so
    # this means "fresh" consistently across both cache-additivity styles.
    fresh_input_tokens = max(prompt_input_tokens - cache_read_tokens - cache_creation_tokens, 0)
    return {
        "event_count": len(events),
        "prompt_input_tokens": prompt_input_tokens,
        "fresh_input_tokens": fresh_input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "verified_cache_read_tokens": verified_cache_read_tokens,
        "verified_cache_creation_tokens": verified_cache_creation_tokens,
        "unverified_cache_read_tokens": unverified_cache_read_tokens,
        "unverified_cache_creation_tokens": unverified_cache_creation_tokens,
        "cache_hit_rate": ratio(cache_read_tokens, prompt_input_tokens),
        "cache_write_rate": ratio(cache_creation_tokens, prompt_input_tokens),
        "cache_reuse_ratio": ratio(cache_read_tokens, cache_creation_tokens),
        "cache_savings_tokens": cache_read_tokens,
    }


__all__ = ["build_cache_summary"]
