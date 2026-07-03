"""Gemini Generate Content adapter (thinking = total_contributing). (Phase 10)

Translates a Gemini *generateContent* payload into a NormalizedUsage. The `usageMetadata`::

    usageMetadata.promptTokenCount         -> input    (total_contributing)
    usageMetadata.candidatesTokenCount     -> output   (total_contributing)
    usageMetadata.cachedContentTokenCount  -> cached_input (subtotal_of "input", 0)
    usageMetadata.thoughtsTokenCount       -> thinking (total_contributing, added ON TOP)
    usageMetadata.totalTokenCount          -> provider_total_tokens (raw)

Unlike OpenAI reasoning (a subtotal of output), Gemini thinking is ADDED to the total, so it
is total_contributing per the INV-4 table. input+output+thinking reconciles to
totalTokenCount, while cachedContent (a subset of the prompt) contributes 0.

Tested against a SIMULATED fixture (documented shape) until a real recorded payload exists.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.base import field_value as _field
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource


class GeminiGenerateContentAdapter(BaseAPISurfaceAdapter):
    """Adapter for the Google Gemini generateContent API surface."""

    provider = "gemini"
    api_surface = "generate_content"

    # promptTokensDetails / candidatesTokensDetails break the count down by modality; each is
    # a subtotal of the input / output it belongs to (TEXT is the bulk and stays in the total).
    _INPUT_MODALITY = {
        "IMAGE": TokenType.IMAGE_INPUT,
        "AUDIO": TokenType.AUDIO_INPUT,
        "VIDEO": TokenType.VIDEO_INPUT,
    }

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        prompt = _field(usage, "promptTokenCount")
        candidates = _field(usage, "candidatesTokenCount")
        cached = _field(usage, "cachedContentTokenCount")
        thoughts = _field(usage, "thoughtsTokenCount")

        if prompt is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, prompt, PrecisionLevel.EXACT, source))
        if candidates is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, candidates, PrecisionLevel.EXACT, source))
        if cached:
            quantities.append(self.build_quantity(TokenType.CACHED_INPUT, cached, PrecisionLevel.EXACT, source))
        if thoughts:
            quantities.append(self.build_quantity(TokenType.THINKING, thoughts, PrecisionLevel.EXACT, source))

        # per-modality breakdown (non-text only; subtotals, contribute 0)
        for detail in _field(usage, "promptTokensDetails", []) or []:
            token_type = self._INPUT_MODALITY.get(_field(detail, "modality"))
            count = _field(detail, "tokenCount")
            if token_type and count:
                quantities.append(self.build_quantity(token_type, count, PrecisionLevel.EXACT, source))
        for detail in _field(usage, "candidatesTokensDetails", []) or []:
            count = _field(detail, "tokenCount")
            if _field(detail, "modality") == "AUDIO" and count:
                quantities.append(self.build_quantity(TokenType.AUDIO_OUTPUT, count, PrecisionLevel.EXACT, source))
        return quantities

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _field(response, "usageMetadata")
        model = _field(response, "modelVersion")
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
            provider_total_tokens=_field(usage, "totalTokenCount"),
            raw_usage=usage if isinstance(usage, dict) else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = _field(event, "usageMetadata")
        if not usage:
            return None
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=_field(event, "modelVersion"),
            quantities=quantities,
            provider_total_tokens=_field(usage, "totalTokenCount"),
        )
