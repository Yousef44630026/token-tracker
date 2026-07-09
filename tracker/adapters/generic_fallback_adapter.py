"""Generic fallback adapter — open capture, closed counting (INV-4 / INV-6 hardening).

For a provider/surface with no dedicated adapter, the choice used to be a ValueError: the
observed call was LOST — the one remaining way real usage could vanish without a flag. This
adapter closes that gap without ever guessing:

  - CAPTURE IS OPEN: it reads the usage object the payload actually carries, recognizing
    only the common key spellings (``prompt_tokens``/``completion_tokens``,
    ``input_tokens``/``output_tokens``, ``promptTokenCount``/``candidatesTokenCount``).
    Anything unrecognized stays in ``raw_usage`` for audit — it never becomes a token type
    and no count is ever invented (INV-3: no fabricated provider fields).
  - COUNTING IS CLOSED: every captured quantity fails closed to ``unverified`` (INV-4) —
    present in the audit trail, EXACT-but-unverified (precision is not trust), contributing 0
    until a dedicated adapter encodes the provider's real additivity truth.

    This adapter assigns ``unverified`` DIRECTLY rather than relying on the central table's
    default. The table is keyed by provider and ignores the surface, so a KNOWN provider on an
    UNKNOWN/unverified surface (e.g. a future ``openai/realtime``) would otherwise resolve to
    ``total_contributing`` and be counted at full confidence with no flag — the opposite of
    fail-closed. The fallback is used precisely when no dedicated, TESTED adapter proves the
    surface's semantics, so its counting must stay closed no matter which provider it is.

The class-level ``provider``/``api_surface`` stay empty so registry auto-discovery skips it;
instances stamp the REAL requested pair so events name the actual provider for the audit.
Instantiate via ``registry.create_adapter_with_fallback`` (the strict ``create_adapter``
contract is unchanged).
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, field_value
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource

# Usage containers and count-key spellings shared by the major wire formats. Deliberately
# short: a spelling not listed here is preserved raw, never mapped by guesswork.
_USAGE_CONTAINERS = ("usage", "usageMetadata")
_INPUT_KEYS = ("prompt_tokens", "input_tokens", "promptTokenCount")
_OUTPUT_KEYS = ("completion_tokens", "output_tokens", "candidatesTokenCount")
_TOTAL_KEYS = ("total_tokens", "totalTokenCount", "totalTokens")


def _well_formed_count(value: Any) -> int | None:
    """Return the value only if it is a usable count; never coerce, never invent."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _first_count(usage: Any, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        count = _well_formed_count(field_value(usage, key))
        if count is not None:
            return count
    return None


class GenericFallbackAdapter(BaseAPISurfaceAdapter):
    """Last-resort adapter for providers without a dedicated one: capture, don't count."""

    provider = ""  # empty at class level: auto-discovery must skip this class
    api_surface = ""

    def __init__(self, provider: str, api_surface: str) -> None:
        if not provider or not api_surface:
            raise ValueError("GenericFallbackAdapter requires the real provider and api_surface")
        self.provider = provider
        self.api_surface = api_surface

    def assign_additivity(self, token_type: TokenType) -> tuple[Additivity, str | None]:
        """Fail closed: a fallback-captured quantity is ALWAYS unverified.

        Overrides the base lookup so a known provider on an unverified surface cannot be
        silently counted via the central table (which is keyed by provider, not surface).
        Contributing 0 + ``unverified_additivity`` until a dedicated adapter proves the surface.
        """
        return Additivity.UNVERIFIED, None

    def _find_usage(self, response: Any) -> Any | None:
        for container in _USAGE_CONTAINERS:
            usage = field_value(response, container)
            if usage is not None:
                return usage
        return None

    def _extract(self, response: Any, source: UsageSource) -> NormalizedUsage:
        usage = self._find_usage(response)
        quantities = []
        provider_total: int | None = None
        if usage is not None:
            input_count = _first_count(usage, _INPUT_KEYS)
            output_count = _first_count(usage, _OUTPUT_KEYS)
            provider_total = _first_count(usage, _TOTAL_KEYS)
            if input_count is not None:
                quantities.append(self.build_quantity(TokenType.INPUT, input_count, PrecisionLevel.EXACT, source))
            if output_count is not None:
                quantities.append(self.build_quantity(TokenType.OUTPUT, output_count, PrecisionLevel.EXACT, source))
        # No usable usage (absent container, or one carrying no recognizable count): flag it,
        # never guess a number for it (INV-6 — absence is surfaced, not zero-filled).
        flags = [] if quantities else ["raw_usage_missing"]
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=field_value(response, "model"),
            quantities=quantities,
            provider_total_tokens=self.reconcile_total(quantities, provider_total),
            data_quality_flags=flags,
            raw_usage=dict(usage) if isinstance(usage, dict) else None,
        )

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        return self._extract(response, UsageSource.PROVIDER_RESPONSE)

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        if self._find_usage(event) is None:
            return None  # a content chunk, not a usage-bearing event
        # Same extraction, but stamped with its true provenance: this usage arrived in a
        # stream's final chunk, not a non-streamed response body.
        return self._extract(event, UsageSource.PROVIDER_STREAM_FINAL)


__all__ = ["GenericFallbackAdapter"]
