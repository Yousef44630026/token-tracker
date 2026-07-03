"""Bedrock Embeddings adapter. (RAG on Bedrock)

Bedrock embedding models (Titan Embeddings, Cohere Embed on Bedrock) are called via
InvokeModel, so the token count is in the model-agnostic Bedrock header
``x-amzn-bedrock-input-token-count`` (there is no output for an embedding). This maps it to a
single ``embedding`` quantity — unlike the generic InvokeModel adapter, which would label it
``input``. Use this when you know the InvokeModel call is an embedding.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.bedrock_invoke_model_adapter import _header_int, _headers
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource

_INPUT_HEADER = "x-amzn-bedrock-input-token-count"


class BedrockEmbeddingsAdapter(BaseAPISurfaceAdapter):
    """Adapter for Bedrock embedding calls (token count from the Bedrock input header)."""

    provider = "bedrock"
    api_surface = "embeddings"

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        count = _header_int(_headers(response), _INPUT_HEADER)
        if count is None:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                data_quality_flags=["raw_usage_missing"],
            )
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            quantities=[self.build_quantity(TokenType.EMBEDDING, count, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)],
            provider_total_tokens=count,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        return None
