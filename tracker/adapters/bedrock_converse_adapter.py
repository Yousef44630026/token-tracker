"""Bedrock Converse adapter (cache buckets are additive). (Phase 10)

Translates a Bedrock *Converse* payload into a NormalizedUsage. The Converse `usage` shape::

    usage.inputTokens
    usage.outputTokens
    usage.totalTokens               -> provider_total_tokens (raw)
    usage.cacheReadInputTokens      -> cached_input          (total_contributing)
    usage.cacheWriteInputTokens     -> cache_creation_input  (total_contributing)

AWS documents ``inputTokens`` as only non-cached input when prompt caching is enabled.
Cache read/write counts are separate additive input buckets, so totalTokens reconciles as
input + cache read + cache write + output. Real cached-payload coverage remains reported
separately; fixture provenance must not change the provider's documented accounting rule.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.base import field_value as _field
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource


class BedrockConverseAdapter(BaseAPISurfaceAdapter):
    """Adapter for the AWS Bedrock Converse API surface."""

    provider = "bedrock"
    api_surface = "converse"
    recognized_usage_token_paths = frozenset(
        {
            "inputTokens",
            "outputTokens",
            "totalTokens",
            "cacheReadInputTokens",
            "cacheWriteInputTokens",
            "cacheDetails[].inputTokens",
            "cacheDetails[].tokenCount",
        }
    )

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        input_tokens = _field(usage, "inputTokens")
        output_tokens = _field(usage, "outputTokens")
        cache_read = _field(usage, "cacheReadInputTokens")
        cache_write = _field(usage, "cacheWriteInputTokens")

        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
        if cache_read:
            quantities.append(self.build_quantity(TokenType.CACHED_INPUT, cache_read, PrecisionLevel.EXACT, source))
        if cache_write:
            quantities.append(self.build_quantity(TokenType.CACHE_CREATION_INPUT, cache_write, PrecisionLevel.EXACT, source))
        return quantities

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _field(response, "usage")
        model = _field(response, "modelId")
        if not usage:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=["raw_usage_missing"],
            )
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_RESPONSE)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=_field(usage, "totalTokens"),
            raw_usage=usage if isinstance(usage, dict) else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        metadata = _field(event, "metadata")
        usage = _field(metadata, "usage") if metadata else _field(event, "usage")
        if not usage:
            return None
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=_field(event, "modelId"),
            quantities=quantities,
            provider_total_tokens=_field(usage, "totalTokens"),
        )
