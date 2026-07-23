"""OpenAI Chat Completions adapter. (Phase 5)

Translates an OpenAI *Chat Completions* payload into a NormalizedUsage. Same provider truth
as the Responses surface (INV-4), only the `usage` field names differ::

    usage.prompt_tokens                                       -> input
    usage.prompt_tokens_details.cached_tokens                 -> subtotal_of "input"
    usage.completion_tokens                                   -> output
    usage.completion_tokens_details.reasoning_tokens          -> subtotal_of "output"
    usage.total_tokens                                        -> provider_total_tokens (raw)

cached/reasoning are subsets of prompt/completion, so they contribute 0 and input+output
already equals total_tokens (no double count).

Tested against a SIMULATED fixture (documented shape) until a real recorded payload exists.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, usage_snapshot
from tracker.adapters.base import field_value as _field
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource


def _finish_reason_flags(response: Any) -> list[str]:
    choices = _field(response, "choices", []) or []
    reasons = {
        _field(choice, "finish_reason")
        for choice in choices
        if _field(choice, "finish_reason") is not None
    }
    flags: list[str] = []
    if reasons.intersection({"length", "content_filter"}):
        flags.append(DataQualityFlag.PROVIDER_RESPONSE_INCOMPLETE.value)
    if "content_filter" in reasons:
        flags.append(DataQualityFlag.CONTENT_FILTER.value)
    return flags


def _usage_completeness_flags(usage: Any) -> list[str]:
    if any(_field(usage, name) is None for name in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return [DataQualityFlag.PROVIDER_USAGE_MISSING.value]
    return []


class OpenAIChatCompletionsAdapter(BaseAPISurfaceAdapter):
    """Adapter for the OpenAI Chat Completions API surface."""

    provider = "openai"
    api_surface = "chat_completions"
    recognized_usage_token_paths = frozenset(
        {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_tokens_details.cached_tokens",
            "prompt_tokens_details.audio_tokens",
            "completion_tokens_details.reasoning_tokens",
            "completion_tokens_details.audio_tokens",
            "completion_tokens_details.accepted_prediction_tokens",
            "completion_tokens_details.rejected_prediction_tokens",
        }
    )

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        prompt = _field(usage, "prompt_tokens")
        completion = _field(usage, "completion_tokens")
        prompt_details = _field(usage, "prompt_tokens_details", {}) or {}
        completion_details = _field(usage, "completion_tokens_details", {}) or {}
        cached = _field(prompt_details, "cached_tokens")
        reasoning = _field(completion_details, "reasoning_tokens")
        audio_in = _field(prompt_details, "audio_tokens")
        audio_out = _field(completion_details, "audio_tokens")

        if prompt is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, prompt, PrecisionLevel.EXACT, source))
        if completion is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, completion, PrecisionLevel.EXACT, source))
        if cached:
            quantities.append(self.build_quantity(TokenType.CACHED_INPUT, cached, PrecisionLevel.EXACT, source))
        if reasoning:
            quantities.append(self.build_quantity(TokenType.REASONING, reasoning, PrecisionLevel.EXACT, source))
        if audio_in:
            quantities.append(self.build_quantity(TokenType.AUDIO_INPUT, audio_in, PrecisionLevel.EXACT, source))
        if audio_out:
            quantities.append(self.build_quantity(TokenType.AUDIO_OUTPUT, audio_out, PrecisionLevel.EXACT, source))
        return quantities

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _field(response, "usage")
        model = _field(response, "model")
        flags = _finish_reason_flags(response)
        if not usage:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=[*flags, "raw_usage_missing"],
            )
        flags.extend(_usage_completeness_flags(usage))
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_RESPONSE)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=_field(usage, "total_tokens"),
            data_quality_flags=flags,
            raw_usage=usage_snapshot(usage),
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = _field(event, "usage")
        flags = _finish_reason_flags(event)
        if not usage:
            if not flags:
                return None
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=_field(event, "model"),
                data_quality_flags=flags,
                stream_terminal=False,
                stream_status="incomplete",
            )
        flags.extend(_usage_completeness_flags(usage))
        quantities = self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=_field(event, "model"),
            quantities=quantities,
            provider_total_tokens=_field(usage, "total_tokens"),
            data_quality_flags=flags,
            raw_usage=usage_snapshot(usage),
            stream_terminal=True,
            stream_status="incomplete" if DataQualityFlag.PROVIDER_RESPONSE_INCOMPLETE.value in flags else "complete",
        )
