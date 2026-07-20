"""AWS Bedrock InvokeModel adapter with model-specific, fail-closed accounting.

Unlike Converse, ``InvokeModel`` has no universal usage schema. Its response body belongs
to the selected model family. AWS does not document token-count response headers for this
API, so header-like values are retained only as unverified evidence and never enter the
canonical total. Exact extraction is limited to documented body contracts implemented here.

Callers should decode boto3's ``StreamingBody`` themselves and provide ``body_json`` or
``body_text``. This adapter deliberately never consumes a stream as a side effect.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage, field_value
from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, UsageSource

_INPUT_HEADER = "x-amzn-bedrock-input-token-count"
_OUTPUT_HEADER = "x-amzn-bedrock-output-token-count"


def _headers(response: Any) -> Mapping[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    metadata = response.get("ResponseMetadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("HTTPHeaders"), Mapping):
        return metadata["HTTPHeaders"]
    headers = response.get("headers")
    return headers if isinstance(headers, Mapping) else {}


def _header_int(headers: Mapping[str, Any], name: str) -> int | None:
    """Return a non-negative integer from a case-insensitive legacy header."""
    for key, value in headers.items():
        if str(key).lower() != name:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 and not isinstance(value, bool) else None
    return None


def _token_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _decoded_body(response: Any) -> Mapping[str, Any] | None:
    """Read a caller-decoded body without consuming boto3's StreamingBody."""
    if not isinstance(response, Mapping):
        return None
    for key in ("body_json", "body"):
        value = response.get(key)
        if isinstance(value, Mapping):
            return value
    body_text = response.get("body_text")
    if isinstance(body_text, bytes):
        body_text = body_text.decode("utf-8", "replace")
    if isinstance(body_text, str):
        try:
            decoded = json.loads(body_text)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, Mapping) else None
    # Tests and integrations may pass an already-decoded model body directly.
    if any(key in response for key in ("usage", "inputTextTokenCount", "results")):
        return response
    return None


def _token_snapshot(value: Any, depth: int = 0) -> Any:
    """Keep token-shaped fields only, avoiding output text/vector duplication."""
    if depth >= 8:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in list(value.items())[:128]:
            key_text = str(key)
            if "token" in key_text.lower():
                result[key_text] = child if isinstance(child, (str, int, float, bool, type(None))) else _token_snapshot(child, depth + 1)
                continue
            nested = _token_snapshot(child, depth + 1)
            if nested not in (None, {}, []):
                result[key_text] = nested
        return result
    if isinstance(value, (list, tuple)):
        result = [_token_snapshot(child, depth + 1) for child in value[:32]]
        return [child for child in result if child not in (None, {}, [])]
    return None


def _model_family(model: str | None, body: Mapping[str, Any]) -> str | None:
    if model:
        lowered = model.lower()
        if "titan" in lowered:
            return "titan_text"
        if "nova" in lowered:
            return "nova"
        if "anthropic" in lowered or "claude" in lowered:
            return "anthropic"
        # An explicit unsupported family must not be reclassified from coincidental fields.
        return None

    signatures = []
    usage = body.get("usage")
    if "inputTextTokenCount" in body or "results" in body:
        signatures.append("titan_text")
    if isinstance(usage, Mapping) and any(key in usage for key in ("inputTokens", "outputTokens", "totalTokens")):
        signatures.append("nova")
    if isinstance(usage, Mapping) and any(key in usage for key in ("input_tokens", "output_tokens")):
        signatures.append("anthropic")
    return signatures[0] if len(signatures) == 1 else None


class BedrockInvokeModelAdapter(BaseAPISurfaceAdapter):
    """Translate documented Titan, Nova, and Anthropic InvokeModel response bodies."""

    provider = "bedrock"
    api_surface = "invoke_model"
    recognized_usage_token_paths = frozenset(
        {
            "inputTextTokenCount",
            "results[].tokenCount",
            "usage.inputTokens",
            "usage.outputTokens",
            "usage.totalTokens",
            "usage.cacheReadInputTokens",
            "usage.cacheWriteInputTokens",
            "usage.input_tokens",
            "usage.output_tokens",
            "usage.cache_read_input_tokens",
            "usage.cache_creation_input_tokens",
            "legacyHeaders.x-amzn-bedrock-input-token-count",
            "legacyHeaders.x-amzn-bedrock-output-token-count",
        }
    )

    def __init__(self, model_id: str | None = None) -> None:
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError("model_id must be a non-empty string when provided")
        self.model_id = model_id.strip() if model_id is not None else None

    def _legacy_header_usage(self, response: Any, source: UsageSource, model: str | None) -> NormalizedUsage | None:
        headers = _headers(response)
        input_tokens = _header_int(headers, _INPUT_HEADER)
        output_tokens = _header_int(headers, _OUTPUT_HEADER)
        quantities = []
        if input_tokens is not None:
            quantities.append(self.build_unverified_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_tokens is not None:
            quantities.append(self.build_unverified_quantity(TokenType.OUTPUT, output_tokens, PrecisionLevel.EXACT, source))
        if not quantities:
            return None
        raw_headers = {}
        if input_tokens is not None:
            raw_headers[_INPUT_HEADER] = input_tokens
        if output_tokens is not None:
            raw_headers[_OUTPUT_HEADER] = output_tokens
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            data_quality_flags=[DataQualityFlag.PROVIDER_USAGE_UNVERIFIED.value],
            raw_usage={"legacyHeaders": raw_headers},
        )

    def _titan_usage(self, body: Mapping[str, Any], source: UsageSource, model: str | None) -> NormalizedUsage:
        input_tokens = _token_int(body.get("inputTextTokenCount"))
        results = body.get("results")
        output_counts = []
        if isinstance(results, list):
            output_counts = [
                count
                for item in results
                if isinstance(item, Mapping)
                if (count := _token_int(item.get("tokenCount"))) is not None
            ]
        quantities = []
        if input_tokens is not None:
            quantities.append(self.build_quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source))
        if output_counts:
            quantities.append(self.build_quantity(TokenType.OUTPUT, sum(output_counts), PrecisionLevel.EXACT, source))
        flags = []
        if input_tokens is None or not output_counts:
            flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            data_quality_flags=flags,
            raw_usage=_token_snapshot(body),
        )

    def _usage_object(
        self,
        usage: Mapping[str, Any],
        source: UsageSource,
        model: str | None,
        *,
        snake_case: bool,
    ) -> NormalizedUsage:
        names = (
            ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", None)
            if snake_case
            else ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheWriteInputTokens", "totalTokens")
        )
        input_key, output_key, cache_read_key, cache_write_key, total_key = names
        fields = {
            TokenType.INPUT: _token_int(usage.get(input_key)),
            TokenType.OUTPUT: _token_int(usage.get(output_key)),
            TokenType.CACHED_INPUT: _token_int(usage.get(cache_read_key)),
            TokenType.CACHE_CREATION_INPUT: _token_int(usage.get(cache_write_key)),
        }
        quantities = [
            self.build_quantity(token_type, count, PrecisionLevel.EXACT, source)
            for token_type, count in fields.items()
            if count is not None and (count > 0 or token_type in {TokenType.INPUT, TokenType.OUTPUT})
        ]
        flags = []
        if fields[TokenType.INPUT] is None or fields[TokenType.OUTPUT] is None:
            flags.append(DataQualityFlag.PROVIDER_USAGE_MISSING.value)
        provider_total = _token_int(usage.get(total_key)) if total_key else None
        if total_key and usage.get(total_key) is not None and provider_total is None:
            flags.append(DataQualityFlag.PROVIDER_RESPONSE_UNPARSEABLE.value)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=provider_total,
            data_quality_flags=flags,
            raw_usage={"usage": _token_snapshot(usage)},
        )

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        response_model = field_value(response, "modelId") or field_value(response, "model_id")
        body = _decoded_body(response)
        body_model = field_value(body, "model") if body is not None else None
        model = response_model or body_model or self.model_id
        family = _model_family(model, body) if body is not None else None

        if body is not None and family == "titan_text":
            return self._titan_usage(body, UsageSource.PROVIDER_RESPONSE, model)
        if body is not None and family in {"nova", "anthropic"}:
            usage = body.get("usage")
            if isinstance(usage, Mapping):
                return self._usage_object(
                    usage,
                    UsageSource.PROVIDER_RESPONSE,
                    model,
                    snake_case=family == "anthropic",
                )

        legacy = self._legacy_header_usage(response, UsageSource.PROVIDER_RESPONSE, model)
        if legacy is not None:
            return legacy
        flags = [DataQualityFlag.RAW_USAGE_MISSING.value]
        if body is not None and _token_snapshot(body):
            flags.append(DataQualityFlag.PROVIDER_RESPONSE_UNPARSEABLE.value)
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            data_quality_flags=flags,
            raw_usage=_token_snapshot(body) if body is not None else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # InvokeModelWithResponseStream ordinary chunks have no documented final usage
        # contract. Do not turn response-envelope headers into a fake terminal event.
        return None


__all__ = [
    "BedrockInvokeModelAdapter",
    "_decoded_body",
    "_header_int",
    "_headers",
    "_token_int",
    "_token_snapshot",
]
