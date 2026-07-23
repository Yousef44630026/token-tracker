"""Gemini Generate Content adapter (thinking = total_contributing). (Phase 10)

Translates a Gemini *generateContent* payload into a NormalizedUsage. The `usageMetadata`::

    usageMetadata.promptTokenCount         -> input    (total_contributing)
    usageMetadata.candidatesTokenCount     -> output   (total_contributing)
    usageMetadata.toolUsePromptTokenCount  -> input/tool_result (total_contributing)
    usageMetadata.cachedContentTokenCount  -> cached_input (subtotal_of "input", 0)
    usageMetadata.thoughtsTokenCount       -> thinking (total_contributing, added ON TOP)
    usageMetadata.totalTokenCount          -> provider_total_tokens (raw)

Unlike OpenAI reasoning (a subtotal of output), Gemini thinking is ADDED to the total, so it
is total_contributing per the INV-4 table. input+tool-result input+output+thinking reconciles
to totalTokenCount, while cachedContent (a subset of the prompt) contributes 0.

Tested against a SIMULATED fixture (documented shape) until a real recorded payload exists.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import (
    BaseAPISurfaceAdapter,
    NormalizedUsage,
    usage_snapshot,
)
from tracker.adapters.base import (
    field_value as _field,
)
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource

_MISSING = object()


def _first_field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        found = _field(value, name, _MISSING)
        if found is not _MISSING:
            return found
    return default


def _modality_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).rsplit(".", 1)[-1].upper()


def _stream_is_terminal(event: Any) -> bool:
    candidates = _field(event, "candidates", []) or []
    for candidate in candidates:
        reason = _first_field(candidate, "finishReason", "finish_reason")
        if reason is not None and _modality_name(reason) not in {"", "NONE", "FINISH_REASON_UNSPECIFIED"}:
            return True
    feedback = _first_field(event, "promptFeedback", "prompt_feedback")
    block_reason = _first_field(feedback, "blockReason", "block_reason") if feedback is not None else None
    return block_reason is not None and _modality_name(block_reason) not in {
        "",
        "NONE",
        "BLOCKED_REASON_UNSPECIFIED",
    }


class GeminiGenerateContentAdapter(BaseAPISurfaceAdapter):
    """Adapter for the Google Gemini generateContent API surface."""

    provider = "gemini"
    api_surface = "generate_content"
    recognized_usage_token_paths = frozenset(
        {
            "promptTokenCount",
            "candidatesTokenCount",
            "cachedContentTokenCount",
            "thoughtsTokenCount",
            "toolUsePromptTokenCount",
            "totalTokenCount",
            "promptTokensDetails[].tokenCount",
            "cacheTokensDetails[].tokenCount",
            "candidatesTokensDetails[].tokenCount",
            "toolUsePromptTokensDetails[].tokenCount",
            "prompt_token_count",
            "candidates_token_count",
            "cached_content_token_count",
            "thoughts_token_count",
            "tool_use_prompt_token_count",
            "total_token_count",
            "prompt_tokens_details[].token_count",
            "cache_tokens_details[].token_count",
            "candidates_tokens_details[].token_count",
            "tool_use_prompt_tokens_details[].token_count",
        }
    )

    # promptTokensDetails / candidatesTokensDetails break the count down by modality; each is
    # a subtotal of the input / output it belongs to (TEXT is the bulk and stays in the total).
    _INPUT_MODALITY = {
        "IMAGE": TokenType.IMAGE_INPUT,
        "AUDIO": TokenType.AUDIO_INPUT,
        "VIDEO": TokenType.VIDEO_INPUT,
    }

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        prompt = _first_field(usage, "promptTokenCount", "prompt_token_count")
        candidates = _first_field(usage, "candidatesTokenCount", "candidates_token_count")
        cached = _first_field(usage, "cachedContentTokenCount", "cached_content_token_count")
        thoughts = _first_field(usage, "thoughtsTokenCount", "thoughts_token_count")
        tool_input = _first_field(usage, "toolUsePromptTokenCount", "tool_use_prompt_token_count")

        if prompt is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, prompt, PrecisionLevel.EXACT, source))
        if candidates is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, candidates, PrecisionLevel.EXACT, source))
        if cached:
            quantities.append(self.build_quantity(TokenType.CACHED_INPUT, cached, PrecisionLevel.EXACT, source))
        if thoughts:
            quantities.append(self.build_quantity(TokenType.THINKING, thoughts, PrecisionLevel.EXACT, source))
        if tool_input:
            quantities.append(
                self.build_quantity(
                    TokenType.INPUT,
                    tool_input,
                    PrecisionLevel.EXACT,
                    source,
                    token_role="tool_result",
                )
            )

        # per-modality breakdown (non-text only; subtotals, contribute 0)
        for detail in _first_field(usage, "promptTokensDetails", "prompt_tokens_details", default=[]) or []:
            token_type = self._INPUT_MODALITY.get(_modality_name(_field(detail, "modality")))
            count = _first_field(detail, "tokenCount", "token_count")
            if token_type and count:
                quantities.append(self.build_quantity(token_type, count, PrecisionLevel.EXACT, source))
        for detail in _first_field(usage, "candidatesTokensDetails", "candidates_tokens_details", default=[]) or []:
            count = _first_field(detail, "tokenCount", "token_count")
            if _modality_name(_field(detail, "modality")) == "AUDIO" and count:
                quantities.append(self.build_quantity(TokenType.AUDIO_OUTPUT, count, PrecisionLevel.EXACT, source))
        return quantities

    @staticmethod
    def _usage_completeness_flags(usage: Any) -> list[str]:
        required_aliases = (
            ("promptTokenCount", "prompt_token_count"),
            ("candidatesTokenCount", "candidates_token_count"),
            ("totalTokenCount", "total_token_count"),
        )
        if any(_first_field(usage, *aliases, default=None) is None for aliases in required_aliases):
            return [DataQualityFlag.PROVIDER_USAGE_MISSING.value]
        return []

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _first_field(response, "usageMetadata", "usage_metadata")
        model = _first_field(response, "modelVersion", "model_version")
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
            provider_total_tokens=_first_field(usage, "totalTokenCount", "total_token_count"),
            data_quality_flags=self._usage_completeness_flags(usage),
            raw_usage=usage_snapshot(usage),
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = _first_field(event, "usageMetadata", "usage_metadata")
        if not usage:
            return None
        terminal = _stream_is_terminal(event)
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=_first_field(event, "modelVersion", "model_version"),
            quantities=quantities,
            provider_total_tokens=_first_field(usage, "totalTokenCount", "total_token_count"),
            data_quality_flags=self._usage_completeness_flags(usage) if terminal else [],
            raw_usage=usage_snapshot(usage),
            stream_terminal=terminal,
        )
