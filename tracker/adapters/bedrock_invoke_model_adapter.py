"""Bedrock InvokeModel adapter (token counts from Bedrock HTTP headers). (Phase 10)

Unlike Converse (one unified usage shape), InvokeModel returns a MODEL-SPECIFIC body
(Titan / Nova / Llama / Cohere / Anthropic-on-Bedrock all differ). The one source of token
counts that is the SAME across every model is the Bedrock response header pair::

    x-amzn-bedrock-input-token-count   -> input  (total_contributing)
    x-amzn-bedrock-output-token-count  -> output (total_contributing)

So this adapter reads those headers (case-insensitively, since boto3/HTTP casing varies) from
``response["ResponseMetadata"]["HTTPHeaders"]`` (the boto3 shape) or a plain ``headers`` dict.
Bedrock provides no total here -> provider_total_tokens is None (never fabricated).

Model-specific body parsing (e.g. Anthropic-on-Bedrock cache fields) is deliberately deferred
until real per-model payloads are captured — guessing body shapes would just encode
assumptions as truth.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource

_INPUT_HEADER = "x-amzn-bedrock-input-token-count"
_OUTPUT_HEADER = "x-amzn-bedrock-output-token-count"


def _headers(response: Any) -> dict:
    if isinstance(response, dict):
        meta = response.get("ResponseMetadata")
        if isinstance(meta, dict) and isinstance(meta.get("HTTPHeaders"), dict):
            return meta["HTTPHeaders"]
        if isinstance(response.get("headers"), dict):
            return response["headers"]
    return {}


def _header_int(headers: dict, name: str) -> int | None:
    """Case-insensitive header lookup, parsed to int (None if absent/unparseable)."""
    for key, value in headers.items():
        if key.lower() == name:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


class BedrockInvokeModelAdapter(BaseAPISurfaceAdapter):
    """Adapter for the AWS Bedrock InvokeModel API surface (header-based token counts)."""

    provider = "bedrock"
    api_surface = "invoke_model"

    def _quantities_from_headers(self, headers: dict, source: UsageSource) -> list:
        quantities = []
        input_tokens = _header_int(headers, _INPUT_HEADER)
        output_tokens = _header_int(headers, _OUTPUT_HEADER)
        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
        return quantities

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        quantities = self._quantities_from_headers(_headers(response), UsageSource.PROVIDER_RESPONSE)
        if not quantities:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                data_quality_flags=["raw_usage_missing"],
            )
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            quantities=quantities,
            provider_total_tokens=None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # The final InvokeModelWithResponseStream chunk carries the same Bedrock header counts.
        quantities = self._quantities_from_headers(_headers(event), UsageSource.PROVIDER_STREAM_FINAL)
        if not quantities:
            return None
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            quantities=quantities,
            provider_total_tokens=None,
        )
