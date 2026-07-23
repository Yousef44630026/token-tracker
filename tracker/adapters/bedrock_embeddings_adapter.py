"""AWS Bedrock embedding adapter with model-specific, fail-closed accounting.

Titan embedding responses document ``inputTextTokenCount``. Cohere Embed responses do not
document a token count, so response-only exact tracking is impossible for that family. AWS
does not document universal InvokeModel token-count headers; if observed, they remain an
unverified ceiling and never alter the canonical total.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, field_value
from tracker.adapters.bedrock_invoke_model_adapter import (
    _decoded_body,
    _header_int,
    _headers,
    _token_int,
    _token_snapshot,
)
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource

_INPUT_HEADER = "x-amzn-bedrock-input-token-count"


class BedrockEmbeddingsAdapter(BaseAPISurfaceAdapter):
    """Extract documented Titan embedding usage; fail closed for other families."""

    provider = "bedrock"
    api_surface = "embeddings"
    recognized_usage_token_paths = frozenset(
        {
            "inputTextTokenCount",
            "legacyHeaders.x-amzn-bedrock-input-token-count",
        }
    )

    def __init__(self, model_id: str | None = None) -> None:
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError("model_id must be a non-empty string when provided")
        self.model_id = model_id.strip() if model_id is not None else None

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        body = _decoded_body(response)
        model = field_value(response, "modelId") or field_value(response, "model_id") or self.model_id
        count = _token_int(body.get("inputTextTokenCount")) if body is not None else None
        titan_model = isinstance(model, str) and model.lower().startswith("amazon.titan-embed-")
        if count is not None and titan_model:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                quantities=[
                    self.build_quantity(
                        TokenType.EMBEDDING,
                        count,
                        PrecisionLevel.EXACT,
                        UsageSource.PROVIDER_RESPONSE,
                    )
                ],
                # Titan exposes an input quantity, not a raw event-level total.
                provider_total_tokens=None,
                raw_usage=_token_snapshot(body),
            )

        if count is not None:
            # ``inputTextTokenCount`` is a Titan contract. Treating the same field as exact
            # on another model family would let a mismatched adapter/model pair fabricate
            # authoritative usage.
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=[
                    DataQualityFlag.PROVIDER_USAGE_MISSING.value,
                    DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value,
                ],
                raw_usage=_token_snapshot(body),
            )

        legacy_count = _header_int(_headers(response), _INPUT_HEADER)
        if legacy_count is not None:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                quantities=[
                    self.build_unverified_quantity(
                        TokenType.EMBEDDING,
                        legacy_count,
                        PrecisionLevel.EXACT,
                        UsageSource.PROVIDER_RESPONSE,
                    )
                ],
                data_quality_flags=[DataQualityFlag.PROVIDER_USAGE_UNVERIFIED.value],
                raw_usage={"legacyHeaders": {_INPUT_HEADER: legacy_count}},
            )

        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            data_quality_flags=[DataQualityFlag.RAW_USAGE_MISSING.value],
            raw_usage=_token_snapshot(body) if body is not None else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        return None
