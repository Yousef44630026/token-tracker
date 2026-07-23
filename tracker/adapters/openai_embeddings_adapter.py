"""OpenAI Embeddings adapter. (RAG)

Translates an OpenAI */v1/embeddings* response into a NormalizedUsage. The `usage` shape is::

    usage.prompt_tokens  -> embedding  (total_contributing — the embedded tokens ARE the cost)
    usage.total_tokens   -> provider_total_tokens (raw; == prompt_tokens, no output)

An embeddings call has no generated output, so it produces a single ``embedding`` quantity
(not input/output). This is the token source for the RAG embedding step — without it, a
RAG pipeline's embedding cost goes untracked.

Tested against a SIMULATED fixture (full documented shape) until a real recorded payload exists.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, usage_snapshot
from tracker.adapters.base import field_value as _field
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource


class OpenAIEmbeddingsAdapter(BaseAPISurfaceAdapter):
    """Adapter for the OpenAI Embeddings API surface."""

    provider = "openai"
    api_surface = "embeddings"
    recognized_usage_token_paths = frozenset({"prompt_tokens", "total_tokens"})

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        prompt = _field(usage, "prompt_tokens")
        if prompt is None:
            return []
        return [self.build_quantity(TokenType.EMBEDDING, prompt, PrecisionLevel.EXACT, source)]

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
        flags = []
        if _field(usage, "prompt_tokens") is None or _field(usage, "total_tokens") is None:
            flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=self._usage_to_quantities(usage, UsageSource.PROVIDER_RESPONSE),
            provider_total_tokens=_field(usage, "total_tokens"),
            data_quality_flags=flags,
            raw_usage=usage_snapshot(usage),
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # Embeddings are not streamed.
        return None
