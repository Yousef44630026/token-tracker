"""OpenAI Responses adapter. (Phase 5)

Translates an OpenAI *Responses* API payload into a NormalizedUsage. The Responses `usage`
shape is::

    usage.input_tokens
    usage.input_tokens_details.cached_tokens     -> subtotal_of "input"
    usage.output_tokens
    usage.output_tokens_details.reasoning_tokens -> subtotal_of "output"
    usage.total_tokens                           -> provider_total_tokens (raw)

cached/reasoning are SUBSETS of input/output, so they are subtotal_of (contribute 0) per the
INV-4 table — summing input+output already equals total_tokens with no double count.

Tested against a SIMULATED fixture (documented shape) until a real recorded payload is
available; the additivity/no-double-count logic exercised here is real either way.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, usage_snapshot
from tracker.adapters.base import field_value as _field
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource

_TERMINAL_STREAM_TYPES = {
    "response.completed": "complete",
    "response.incomplete": "incomplete",
    "response.failed": "failed",
}


class OpenAIResponsesAdapter(BaseAPISurfaceAdapter):
    """Adapter for the OpenAI Responses API surface."""

    provider = "openai"
    api_surface = "responses"
    recognized_usage_token_paths = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details.cached_tokens",
            "input_tokens_details.audio_tokens",
            "output_tokens_details.reasoning_tokens",
            "output_tokens_details.audio_tokens",
        }
    )

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        quantities = []
        input_tokens = _field(usage, "input_tokens")
        output_tokens = _field(usage, "output_tokens")
        input_details = _field(usage, "input_tokens_details", {}) or {}
        output_details = _field(usage, "output_tokens_details", {}) or {}
        cached = _field(input_details, "cached_tokens")
        reasoning = _field(output_details, "reasoning_tokens")
        audio_in = _field(input_details, "audio_tokens")
        audio_out = _field(output_details, "audio_tokens")

        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
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
            provider_total_tokens=_field(usage, "total_tokens"),
            raw_usage=usage_snapshot(usage),
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        event_type = _field(event, "type")
        nested_response = _field(event, "response")
        response = nested_response if nested_response is not None else event
        usage = _field(response, "usage")
        terminal_status = _TERMINAL_STREAM_TYPES.get(event_type)
        # Backward compatibility for callers that pass the final response object itself
        # rather than the documented lifecycle envelope.
        terminal = terminal_status is not None or event_type is None
        if not usage and not terminal_status:
            return None
        flags = []
        if terminal_status == "incomplete":
            flags.append(DataQualityFlag.PROVIDER_RESPONSE_INCOMPLETE.value)
        elif terminal_status == "failed":
            flags.append(DataQualityFlag.PROVIDER_RESPONSE_FAILED.value)
        if not usage:
            flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
            quantities = []
        else:
            source = UsageSource.PROVIDER_STREAM_FINAL if terminal else UsageSource.PROVIDER_STREAM_PARTIAL
            quantities = self._usage_to_quantities(usage, source)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=_field(response, "model"),
            quantities=quantities,
            provider_total_tokens=_field(usage, "total_tokens") if usage else None,
            data_quality_flags=flags,
            raw_usage=usage_snapshot(usage),
            stream_terminal=terminal,
            stream_status=terminal_status,
        )
