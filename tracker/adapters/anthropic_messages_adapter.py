"""Anthropic Messages adapter (cache buckets are additive, no provider total).

Translates an Anthropic *Messages* payload into a NormalizedUsage. The `usage` shape::

    usage.input_tokens                 -> input  (total_contributing)
    usage.output_tokens                -> output (total_contributing)
    usage.cache_read_input_tokens      -> cached_input          (total_contributing)
    usage.cache_creation_input_tokens  -> cache_creation_input  (total_contributing)

Two provider specifics, both handled honestly:
  - Anthropic reports NO total field -> provider_total_tokens is None (we never fabricate
    one); event_total_mismatch is therefore None (nothing to reconcile against).
  - cache_* tokens are reported SEPARATELY from input_tokens (not a subset like OpenAI).
    Therefore input + cache_read + cache_creation are all contributing input buckets.

Streaming note: Anthropic splits usage across message_start (input) and message_delta
(output); a full stream is reconciled by the stream tracker. extract_usage_from_stream_event
here handles an event that already carries a consolidated `usage`.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.base import field_value as _field
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource


class AnthropicMessagesAdapter(BaseAPISurfaceAdapter):
    """Adapter for the Anthropic Messages API surface."""

    provider = "anthropic"
    api_surface = "messages"

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        input_tokens = _field(usage, "input_tokens")
        output_tokens = _field(usage, "output_tokens")
        cache_read = _field(usage, "cache_read_input_tokens")
        cache_creation = _field(usage, "cache_creation_input_tokens")
        cache_creation_details = _field(usage, "cache_creation", {}) or {}

        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
        if cache_read:
            quantities.append(self.build_quantity(TokenType.CACHED_INPUT, cache_read, PrecisionLevel.EXACT, source))
        if cache_creation:
            detail_metadata = {}
            ephemeral_5m = _field(
                cache_creation_details,
                "ephemeral_5m_input_tokens",
            )
            ephemeral_1h = _field(
                cache_creation_details,
                "ephemeral_1h_input_tokens",
            )
            if ephemeral_5m is not None:
                detail_metadata["ephemeral_5m_input_tokens"] = ephemeral_5m
            if ephemeral_1h is not None:
                detail_metadata["ephemeral_1h_input_tokens"] = ephemeral_1h
            quantities.append(
                self.build_quantity(
                    TokenType.CACHE_CREATION_INPUT,
                    cache_creation,
                    PrecisionLevel.EXACT,
                    source,
                    metadata=detail_metadata,
                )
            )
        return quantities

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _field(response, "usage")
        model = _field(response, "model")
        if not usage:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=["raw_usage_missing"],
            )
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_RESPONSE)
        # Anthropic provides no total field -> provider_total_tokens stays None (never fabricated)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=None,
            raw_usage=usage if isinstance(usage, dict) else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # Anthropic SPLITS usage across the stream: message_start carries the input under
        # message.usage; message_delta carries the output under a top-level usage. Handle both.
        message = _field(event, "message")
        usage = _field(event, "usage")
        if not usage and message is not None:
            usage = _field(message, "usage")
        if not usage:
            return None
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL)
        model = _field(message, "model") if message is not None else _field(event, "model")
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=None,
        )
