"""Vertex AI text-embeddings adapter.

Vertex exposes processed input-token counts through multiple official surfaces. The REST
``predict`` shape uses ``predictions[].embeddings.statistics.token_count``; the Google Gen AI
SDK exposes ``embeddings[].statistics.token_count``; and the v1 ``embedContent`` surface uses
``usageMetadata.promptTokenCount``. Per-item counts are summed, while a response-level total
is retained only when the provider actually sends it. A partial count remains a measured
floor and is paired with an UNKNOWN quantity instead of being promoted to a false exact total.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, field_value, usage_snapshot
from tracker.models.enums import (
    DataQualityFlag,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)


def _first_field(value: Any, *names: str, default: Any = None) -> Any:
    sentinel = object()
    for name in names:
        found = field_value(value, name, sentinel)
        if found is not sentinel:
            return found
    return default


def _token_count(value: Any) -> int | None:
    """Accept documented integer counts and SDK integral floats, never strings/bools."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _without_vectors(value: Any) -> dict[str, Any] | None:
    """Keep token-related SDK fields inspectable without retaining embedding vectors."""
    snapshot = usage_snapshot(value)
    if not snapshot:
        return snapshot

    def strip(current: Any) -> Any:
        if isinstance(current, Mapping):
            return {
                key: strip(child)
                for key, child in current.items()
                if str(key) not in {"values", "embedding"}
            }
        if isinstance(current, list):
            return [strip(child) for child in current]
        return current

    stripped = strip(snapshot)
    return stripped if isinstance(stripped, dict) else None


class VertexAIEmbeddingsAdapter(BaseAPISurfaceAdapter):
    """Adapter for Vertex AI ``predict`` and Gen AI SDK text embeddings."""

    provider = "vertex_ai"
    api_surface = "embeddings"
    recognized_usage_token_paths = frozenset(
        {
            "predictions[].embeddings.statistics.token_count",
            "predictions[].embeddings.statistics.tokenCount",
            "embeddings[].statistics.token_count",
            "embeddings[].statistics.tokenCount",
            "usageMetadata.promptTokenCount",
            "usageMetadata.prompt_token_count",
            "usageMetadata.totalTokenCount",
            "usageMetadata.total_token_count",
        }
    )

    def __init__(self, model_id: str | None = None) -> None:
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError("model_id must be a non-empty string when provided")
        self.model_id = model_id.strip() if model_id is not None else None

    @staticmethod
    def _embedding_records(response: Any) -> list[Any]:
        sdk_embeddings = field_value(response, "embeddings")
        if sdk_embeddings is not None:
            return list(sdk_embeddings or [])
        predictions = field_value(response, "predictions")
        if predictions is None:
            return []
        return [field_value(prediction, "embeddings", prediction) for prediction in predictions or []]

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage_metadata = _first_field(response, "usageMetadata", "usage_metadata")
        records = self._embedding_records(response)
        prompt_count = (
            _token_count(_first_field(usage_metadata, "promptTokenCount", "prompt_token_count"))
            if usage_metadata is not None
            else None
        )
        record_counts = [
            _token_count(_first_field(field_value(record, "statistics"), "token_count", "tokenCount"))
            for record in records
        ]
        metadata_masks_records = (
            usage_metadata is not None
            and prompt_count in {None, 0}
            and any(count is not None and count > 0 for count in record_counts)
        )
        if usage_metadata is not None and not metadata_masks_records:
            provider_total = _token_count(
                _first_field(usage_metadata, "totalTokenCount", "total_token_count")
            )
            model = _first_field(response, "model", "modelVersion", "model_version", default=self.model_id)
            truncated = bool(field_value(response, "truncated", False))
            flags = []
            quantities = []
            if prompt_count is None:
                flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
                quantities.append(
                    self.build_quantity(
                        TokenType.EMBEDDING,
                        None,
                        PrecisionLevel.UNKNOWN,
                        UsageSource.NONE,
                        unknown_reason=UnknownReason.PROVIDER_OMITTED,
                    )
                )
            else:
                quantities.append(
                    self.build_quantity(
                        TokenType.EMBEDDING,
                        prompt_count,
                        PrecisionLevel.EXACT,
                        UsageSource.PROVIDER_RESPONSE,
                        metadata={"embedding_count": 1, "truncated_input_count": int(truncated)},
                    )
                )
            if truncated:
                flags.append(DataQualityFlag.PROVIDER_INPUT_TRUNCATED.value)
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                quantities=quantities,
                provider_total_tokens=provider_total,
                data_quality_flags=flags,
                raw_usage={"usageMetadata": usage_snapshot(usage_metadata)},
            )

        model = _first_field(response, "model", "modelVersion", "model_version", default=self.model_id)
        if not records:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=[DataQualityFlag.RAW_USAGE_MISSING.value],
            )

        known_counts: list[int] = []
        missing_count = 0
        truncated_count = 0
        raw_records = []
        for record in records:
            statistics = field_value(record, "statistics")
            count = _token_count(_first_field(statistics, "token_count", "tokenCount"))
            if count is None:
                missing_count += 1
            else:
                known_counts.append(count)
            if bool(field_value(statistics, "truncated", False)):
                truncated_count += 1
            raw_records.append(_without_vectors(record) or {})

        quantities = []
        known_total = sum(known_counts)
        if known_counts:
            quantities.append(
                self.build_quantity(
                    TokenType.EMBEDDING,
                    known_total,
                    PrecisionLevel.EXACT,
                    UsageSource.PROVIDER_RESPONSE,
                    metadata={
                        "embedding_count": len(records),
                        "counted_embedding_count": len(known_counts),
                        "truncated_input_count": truncated_count,
                    },
                )
            )
        if missing_count:
            quantities.append(
                self.build_quantity(
                    TokenType.EMBEDDING,
                    None,
                    PrecisionLevel.UNKNOWN,
                    UsageSource.NONE,
                    unknown_reason=UnknownReason.PROVIDER_OMITTED,
                    metadata={"missing_embedding_count": missing_count},
                )
            )

        flags = []
        if missing_count:
            flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
        if metadata_masks_records:
            flags.append(DataQualityFlag.PROVIDER_SCHEMA_DRIFT.value)
        if truncated_count:
            flags.append(DataQualityFlag.PROVIDER_INPUT_TRUNCATED.value)
        if field_value(response, "embeddings") is not None:
            raw_usage = {"embeddings": raw_records}
        else:
            raw_usage = {"predictions": [{"embeddings": record} for record in raw_records]}
        if metadata_masks_records and raw_usage is not None:
            raw_usage["usageMetadata"] = usage_snapshot(usage_metadata)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=None,
            data_quality_flags=flags,
            raw_usage=raw_usage,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # Vertex text embeddings are returned as one response, not token-streamed.
        return None


__all__ = ["VertexAIEmbeddingsAdapter"]
