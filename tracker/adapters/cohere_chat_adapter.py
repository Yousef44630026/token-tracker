"""Cohere Chat adapter. (additional provider)

Cohere v2 chat reports usage differently from OpenAI::

    usage.tokens.input_tokens / output_tokens         (raw counts — preferred)
    usage.billed_units.input_tokens / output_tokens   (billable counts — fallback)

Cohere gives no single total field, so provider_total_tokens stays None (never fabricated);
input + output is the contributing total.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, usage_snapshot
from tracker.adapters.base import field_value as _field
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource


class CohereChatAdapter(BaseAPISurfaceAdapter):
    """Adapter for the Cohere chat API surface."""

    provider = "cohere"
    api_surface = "chat"
    recognized_usage_token_paths = frozenset(
        {
            "tokens.input_tokens",
            "tokens.output_tokens",
            "billed_units.input_tokens",
            "billed_units.output_tokens",
        }
    )

    def _usage_to_quantities(self, usage: Any, source: UsageSource) -> list:
        # `tokens` (raw counts) is preferred over `billed_units` (billing-adjusted) — but a
        # PRESENT-though-empty `tokens: {}` must still win over falling back to billed_units;
        # only a genuinely ABSENT `tokens` key should trigger the fallback.
        tokens = _field(usage, "tokens")
        counts = tokens if tokens is not None else (_field(usage, "billed_units") or {})
        input_tokens = _field(counts, "input_tokens")
        output_tokens = _field(counts, "output_tokens")
        quantities = []
        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
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
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=self._usage_to_quantities(usage, UsageSource.PROVIDER_RESPONSE),
            provider_total_tokens=None,
            raw_usage=usage_snapshot(usage),
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # The Cohere stream's message-end event carries usage under delta.usage (or usage).
        usage = _field(event, "usage") or _field(_field(event, "delta", {}) or {}, "usage")
        if not usage:
            return None
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            quantities=self._usage_to_quantities(usage, UsageSource.PROVIDER_STREAM_FINAL),
            provider_total_tokens=None,
            raw_usage=usage_snapshot(usage),
            stream_terminal=True,
        )
