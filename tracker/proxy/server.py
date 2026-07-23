"""Streaming-safe loopback reverse proxy for real provider-call verification."""

from __future__ import annotations

import codecs
import hashlib
import http.client
import ipaddress
import json
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.registry import create_adapter_with_fallback
from tracker.context.headers import extract as extract_context
from tracker.context.model import TraceContext, new_trace
from tracker.models.enums import (
    Additivity,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.normalization.additivity import assign_additivity
from tracker.normalization.event_builder import build_event
from tracker.normalization.normalizer import normalize
from tracker.normalization.usage_contract import inspect_usage_contract, usage_contract_observation
from tracker.proxy.estimator import (
    PromptEstimate,
    estimate_prompt,
    extract_latest_user_text,
)
from tracker.storage.file_repository import FileRepository
from tracker.streaming.status import merge_stream_status

Estimator = Callable[[dict[str, Any], str, str], PromptEstimate]
ObservationCallback = Callable[[TokenEvent], None]

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

_DEFAULT_UPSTREAMS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
}


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    """Reverse-proxy settings. Binding is deliberately restricted to loopback."""

    provider: str
    upstream_base_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    timeout_seconds: float = 300.0
    max_request_body_bytes: int = 16 * 1024 * 1024
    record_success_without_usage: bool = False
    prompt_suite_sequence: int | None = None
    prompt_suite_label: str | None = None
    prompt_suite_fingerprint: str | None = None
    prompt_suite_source: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("provider must be a non-empty string")
        # A provider without a dedicated adapter is proxyable (captured open / counted
        # closed via the generic fallback adapter), but there is no default upstream to
        # guess for it — the operator must name the upstream explicitly. This also keeps
        # a typo'd known provider ('opnai') loud instead of silently un-defaulted.
        if provider not in _DEFAULT_UPSTREAMS and not self.upstream_base_url:
            raise ValueError(f"unknown provider {provider!r} has no default upstream; pass upstream_base_url explicitly")
        object.__setattr__(self, "provider", provider)
        if not _is_loopback(self.host):
            raise ValueError("the real-call proxy may bind only to a loopback address")
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.timeout_seconds <= 0 or self.max_request_body_bytes <= 0:
            raise ValueError("proxy limits must be positive")
        if self.prompt_suite_sequence is not None:
            if (
                isinstance(self.prompt_suite_sequence, bool)
                or not isinstance(self.prompt_suite_sequence, int)
                or self.prompt_suite_sequence <= 0
            ):
                raise ValueError("prompt_suite_sequence must be a positive integer")
        upstream = self.upstream_base_url or _DEFAULT_UPSTREAMS[provider]
        parsed = urlsplit(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("upstream_base_url must be an absolute HTTP(S) URL")
        object.__setattr__(self, "upstream_base_url", upstream.rstrip("/"))


@dataclass(slots=True)
class _RequestMeasurement:
    request_hash: str
    estimate: PromptEstimate
    adapter: BaseAPISurfaceAdapter
    context: TraceContext
    started_at: float
    proxy_session_id: str
    request_sequence: int
    prompt_fingerprint: str | None
    prompt_sequence: int | None
    prompt_cycle: int | None
    prompt_suite_sequence: int | None
    prompt_suite_label: str | None
    prompt_suite_fingerprint: str | None
    prompt_suite_source: str | None


class _UsageAccumulator:
    """Merge split provider stream usage into one final event."""

    def __init__(self, adapter: BaseAPISurfaceAdapter) -> None:
        self.adapter = adapter
        self.partial_quantities: dict[tuple[TokenType, str | None], Any] = {}
        self.terminal_quantities: dict[tuple[TokenType, str | None], Any] = {}
        self.saw_terminal_usage = False
        self.provider_total_tokens: int | None = None
        self.model: str | None = None
        self.provider_response_id: str | None = None
        self.stream_status: str | None = None
        self.flags: list[str] = []
        self.unmapped_usage_fields: set[str] = set()

    def feed(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        candidates = [payload]
        response = payload.get("response")
        if isinstance(response, dict):
            candidates.append(response)
        message = payload.get("message")
        if isinstance(message, dict):
            candidates.append(message)

        for candidate in candidates:
            candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
            if isinstance(candidate_id, str) and candidate_id:
                self.provider_response_id = candidate_id
                break

        usage: NormalizedUsage | None = None
        for candidate in candidates:
            try:
                usage = self.adapter.extract_usage_from_stream_event(candidate)
            except Exception:  # noqa: BLE001 - malformed stream events are ignored
                usage = None
            if usage is not None:
                break
        if usage is None:
            return

        usage_flags, unmapped_paths = inspect_usage_contract(self.adapter, usage)
        self.unmapped_usage_fields.update(unmapped_paths)
        self.model = usage.model or self.model
        terminal = usage.stream_terminal
        if terminal is None:
            terminal = any(quantity.token_type == TokenType.OUTPUT for quantity in usage.quantities)
        if terminal and usage.provider_total_tokens is not None:
            self.provider_total_tokens = usage.provider_total_tokens
        self.stream_status = merge_stream_status(self.stream_status, usage.stream_status)
        for flag in usage_flags:
            if flag not in self.flags:
                self.flags.append(flag)
        target = self.terminal_quantities if terminal else self.partial_quantities
        for quantity in usage.quantities:
            target[(quantity.token_type, quantity.token_role)] = quantity
        if terminal and usage.quantities:
            self.terminal_quantities = {**self.partial_quantities, **self.terminal_quantities}
            self.saw_terminal_usage = True

    @property
    def quantities(self) -> dict[tuple[TokenType, str | None], Any]:
        return self.terminal_quantities if self.saw_terminal_usage else self.partial_quantities

    def build_event(
        self,
        *,
        context: TraceContext,
        request_hash: str,
        response_hash: str,
        observation: dict[str, Any],
    ) -> TokenEvent | None:
        if not self.quantities:
            return None
        event_observation = dict(observation)
        if self.stream_status is not None:
            event_observation["status"] = self.stream_status
        event_observation.update(
            usage_contract_observation(sorted(self.unmapped_usage_fields))
        )
        return build_event(
            context=context,
            provider=self.adapter.provider,
            api_surface=self.adapter.api_surface,
            model=self.model,
            quantities=list(self.quantities.values()),
            provider_total_tokens=self.provider_total_tokens,
            leading_flags=self.flags,
            request_hash=request_hash,
            response_hash=response_hash,
            observation=event_observation,
        )


class _SSEParser:
    """Incrementally decode SSE data records and pass JSON payloads to an accumulator."""

    # terminal markers: seeing one means the provider finished the stream on purpose.
    # Their absence at EOF means the stream was truncated (upstream died / connection cut).
    _TERMINAL_EVENT_TYPES = {"message_stop", "response.completed", "response.incomplete", "response.failed"}

    def __init__(self, accumulator: _UsageAccumulator) -> None:
        self.accumulator = accumulator
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.buffer = ""
        self.data_lines: list[str] = []
        self.saw_output_delta = False
        self.saw_terminal = False

    def feed(self, data: bytes) -> None:
        self.buffer += self.decoder.decode(data)
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._line(line.rstrip("\r"))

    def finish(self) -> None:
        self.buffer += self.decoder.decode(b"", final=True)
        if self.buffer:
            self._line(self.buffer.rstrip("\r"))
        self._dispatch()

    def _line(self, line: str) -> None:
        if not line:
            self._dispatch()
        elif line.startswith("data:"):
            self.data_lines.append(line[5:].lstrip())

    def _dispatch(self) -> None:
        if not self.data_lines:
            return
        data = "\n".join(self.data_lines)
        self.data_lines.clear()
        if data == "[DONE]":
            self.saw_terminal = True
            return
        try:
            payload = json.loads(data)
        except (json.JSONDecodeError, UnicodeError):
            return
        event_type = payload.get("type") if isinstance(payload, dict) else None
        if event_type in self._TERMINAL_EVENT_TYPES:
            self.saw_terminal = True
        delta = payload.get("delta") if isinstance(payload, dict) else None
        if event_type in {
            "content_block_delta",
            "response.output_text.delta",
            "response.content_part.delta",
        }:
            if event_type != "content_block_delta" or (isinstance(delta, dict) and delta.get("type") in {"text_delta", "input_json_delta"}):
                self.saw_output_delta = True
        self.accumulator.feed(payload)


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _surface(provider: str, path: str) -> str | None:
    clean_path = urlsplit(path).path.rstrip("/")
    if provider == "anthropic" and clean_path.endswith("/messages"):
        return "messages"
    if provider == "openai" and clean_path.endswith("/responses"):
        return "responses"
    if provider == "openai" and clean_path.endswith("/chat/completions"):
        return "chat_completions"
    # Generic path shapes for any other provider (groq, together, OpenAI-compatible
    # gateways...): the surface is just the measurement label; the adapter is resolved with
    # fallback, so an unfamiliar provider is captured open / counted closed instead of
    # passing through UNMEASURED. Non-usage paths (/models, /files...) still return None.
    if clean_path.endswith("/chat/completions"):
        return "chat_completions"
    if clean_path.endswith("/responses"):
        return "responses"
    if clean_path.endswith("/messages"):
        return "messages"
    if clean_path.endswith("/embeddings"):
        return "embeddings"
    return None


def _provider_request_id(upstream: http.client.HTTPResponse) -> str | None:
    for header_name in (
        "request-id",
        "x-request-id",
        "anthropic-request-id",
        "openai-request-id",
    ):
        value = upstream.getheader(header_name)
        if value:
            return value
    return None


def _observation(
    measurement: _RequestMeasurement,
    *,
    status: str,
    authoritative: bool,
    streaming: bool,
    http_status: int | None,
    provider_request_id: str | None,
    provider_response_id: str | None,
    response_headers_ms: float | None,
    time_to_first_token_ms: float | None,
    duration_ms: float,
    error_type: str | None = None,
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "status": status,
        "authoritative": authoritative,
        "streaming": streaming,
        "http_status": http_status,
        "provider_request_id": provider_request_id,
        "provider_response_id": provider_response_id,
        "proxy_session_id": measurement.proxy_session_id,
        "request_sequence": measurement.request_sequence,
        "prompt_fingerprint": measurement.prompt_fingerprint,
        "prompt_sequence": measurement.prompt_sequence,
        "prompt_cycle": measurement.prompt_cycle,
        "response_headers_ms": (round(response_headers_ms, 3) if response_headers_ms is not None else None),
        "time_to_first_token_ms": (round(time_to_first_token_ms, 3) if time_to_first_token_ms is not None else None),
        "duration_ms": round(duration_ms, 3),
    }
    if measurement.prompt_suite_sequence is not None:
        observation["suite_prompt_sequence"] = measurement.prompt_suite_sequence
    if measurement.prompt_suite_label:
        observation["suite_prompt_label"] = measurement.prompt_suite_label
    if measurement.prompt_suite_fingerprint:
        observation["suite_prompt_fingerprint"] = measurement.prompt_suite_fingerprint
    if measurement.prompt_suite_source:
        observation["suite_prompt_source"] = measurement.prompt_suite_source
    if error_type:
        observation["error_type"] = error_type
    return observation


def _response_comparison(event: TokenEvent, estimate: PromptEstimate) -> None:
    prompt_types = {
        TokenType.INPUT,
        TokenType.CACHED_INPUT,
        TokenType.CACHE_CREATION_INPUT,
    }
    prompt_quantities = [
        quantity
        for quantity in event.quantities
        if quantity.token_type in prompt_types
        and quantity.quantity is not None
        and quantity.precision_level == PrecisionLevel.EXACT
        and quantity.additivity == Additivity.TOTAL_CONTRIBUTING
    ]
    exact_input = next(
        (quantity for quantity in prompt_quantities if quantity.token_type == TokenType.INPUT),
        None,
    )
    metadata_target = exact_input or (prompt_quantities[0] if prompt_quantities else None)
    if metadata_target is None:
        return
    provider_prompt_tokens = sum(quantity.quantity or 0 for quantity in prompt_quantities)
    difference = provider_prompt_tokens - estimate.quantity
    components = {quantity.token_type.value: quantity.quantity for quantity in prompt_quantities}
    metadata: dict[str, Any] = {
        "quantity": estimate.quantity,
        "precision_level": PrecisionLevel.ESTIMATE.value,
        "usage_source": UsageSource.LOCAL_TOKENIZER.value,
        "estimator": estimate.estimator,
        "text_characters": estimate.text_characters,
        "prompt_text_sha256": estimate.text_sha256,
        "provider_input_tokens": exact_input.quantity if exact_input is not None else None,
        "provider_prompt_tokens": provider_prompt_tokens,
        "provider_prompt_components": components,
        "provider_minus_estimate": difference,
        "absolute_error": abs(difference),
        "absolute_percentage_error": (round(abs(difference) / provider_prompt_tokens * 100, 4) if provider_prompt_tokens else None),
    }
    metadata_target.metadata = dict(metadata_target.metadata)
    metadata_target.metadata["prompt_estimate"] = metadata


def _estimate_only_event(
    measurement: _RequestMeasurement,
    *,
    response_hash: str,
    flags: list[str],
    observation: dict[str, Any],
) -> TokenEvent:
    estimate_metadata = {
        "prompt_estimate": {
            "quantity": measurement.estimate.quantity,
            "precision_level": PrecisionLevel.ESTIMATE.value,
            "usage_source": UsageSource.LOCAL_TOKENIZER.value,
            "estimator": measurement.estimate.estimator,
            "text_characters": measurement.estimate.text_characters,
            "prompt_text_sha256": measurement.estimate.text_sha256,
        }
    }
    input_quantity = measurement.adapter.build_quantity(
        TokenType.INPUT,
        measurement.estimate.quantity,
        PrecisionLevel.ESTIMATE,
        UsageSource.LOCAL_TOKENIZER,
        metadata=estimate_metadata,
    )
    output_quantity = measurement.adapter.build_quantity(
        TokenType.OUTPUT,
        None,
        PrecisionLevel.UNKNOWN,
        UsageSource.NONE,
        unknown_reason=UnknownReason.PROVIDER_OMITTED,
    )
    return build_event(
        context=measurement.context,
        provider=measurement.adapter.provider,
        api_surface=measurement.adapter.api_surface,
        model=None,
        quantities=[input_quantity, output_quantity],
        provider_total_tokens=None,
        leading_flags=[*flags, "input_estimate_only"],
        request_hash=measurement.request_hash,
        response_hash=response_hash,
        observation=observation,
    )


def _merge_path(upstream_base_url: str, incoming_path: str) -> tuple[Any, str]:
    parsed = urlsplit(upstream_base_url)
    incoming = urlsplit(incoming_path)
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}{incoming.path or '/'}"
    if incoming.query:
        path += f"?{incoming.query}"
    return parsed, path


def _make_handler(
    config: ProxyConfig,
    repository: FileRepository,
    *,
    estimator: Estimator,
    on_event: ObservationCallback | None,
) -> type[BaseHTTPRequestHandler]:
    proxy_session_id = f"proxy-{uuid.uuid4().hex[:16]}"
    sequence_lock = Lock()
    request_sequence = 0
    prompt_sequence = 0
    prompt_state: dict[str, tuple[int, int]] = {}

    class _ProxyHandler(BaseHTTPRequestHandler):
        server_version = "AITokenTrackerProxy/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            if urlsplit(self.path).path == "/healthz":
                self._send_local(200, {"status": "ok", "provider": config.provider})
                return
            self._proxy()

        def do_POST(self) -> None:  # noqa: N802
            self._proxy()

        def do_DELETE(self) -> None:  # noqa: N802
            self._proxy()

        def do_PUT(self) -> None:  # noqa: N802
            self._proxy()

        def do_PATCH(self) -> None:  # noqa: N802
            self._proxy()

        def _send_local(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(dict(payload)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                return b""
            length = int(length_header)
            if length < 0 or length > config.max_request_body_bytes:
                raise ValueError("request body exceeds proxy limit")
            return self.rfile.read(length)

        def _measurement(
            self,
            body: bytes,
            *,
            started_at: float,
        ) -> _RequestMeasurement | None:
            nonlocal prompt_sequence, request_sequence
            api_surface = _surface(config.provider, self.path)
            if api_surface is None or not body:
                return None
            try:
                decoded = json.loads(body)
            except (json.JSONDecodeError, UnicodeError):
                return None
            if not isinstance(decoded, dict):
                return None
            adapter = create_adapter_with_fallback(config.provider, api_surface)
            latest_user_text = extract_latest_user_text(
                decoded,
                config.provider,
                api_surface,
            )
            prompt_fingerprint = hashlib.sha256(latest_user_text.encode("utf-8")).hexdigest() if latest_user_text else None
            context = extract_context(dict(self.headers)) or new_trace(
                workflow="real_call_proxy",
                environment="local",
            )
            with sequence_lock:
                request_sequence += 1
                sequence = request_sequence
                current_prompt_sequence: int | None = None
                current_prompt_cycle: int | None = None
                if prompt_fingerprint is not None:
                    known = prompt_state.get(prompt_fingerprint)
                    if known is None:
                        prompt_sequence += 1
                        current_prompt_sequence = prompt_sequence
                        current_prompt_cycle = 1
                    else:
                        current_prompt_sequence = known[0]
                        current_prompt_cycle = known[1] + 1
                    prompt_state[prompt_fingerprint] = (
                        current_prompt_sequence,
                        current_prompt_cycle,
                    )
            return _RequestMeasurement(
                request_hash=hashlib.sha256(body).hexdigest(),
                estimate=estimator(decoded, config.provider, api_surface),
                adapter=adapter,
                context=context,
                started_at=started_at,
                proxy_session_id=proxy_session_id,
                request_sequence=sequence,
                prompt_fingerprint=prompt_fingerprint,
                prompt_sequence=current_prompt_sequence,
                prompt_cycle=current_prompt_cycle,
                prompt_suite_sequence=config.prompt_suite_sequence,
                prompt_suite_label=config.prompt_suite_label,
                prompt_suite_fingerprint=config.prompt_suite_fingerprint,
                prompt_suite_source=config.prompt_suite_source,
            )

        def _upstream_headers(self, body: bytes) -> dict[str, str]:
            headers: dict[str, str] = {}
            for key, value in self.headers.items():
                lowered = key.lower()
                if lowered in _HOP_BY_HOP or lowered in {
                    "host",
                    "content-length",
                    "accept-encoding",
                    "expect",
                }:
                    continue
                if lowered.startswith("x-tokentracker-"):
                    continue
                headers[key] = value
            if body:
                headers["Content-Length"] = str(len(body))
            headers["Accept-Encoding"] = "identity"
            return headers

        def _persist(self, event: TokenEvent) -> None:
            try:
                repository.append_unique([event])
                if on_event is not None:
                    on_event(event)
            except Exception:  # noqa: BLE001 - observation must never break the API call
                return

        def _proxy(self) -> None:
            started_at = monotonic()
            try:
                body = self._read_body()
            except (TypeError, ValueError, OSError):
                self._send_local(413, {"error": "request_body_rejected"})
                return

            # Defense in depth for the "observation must never break the API call" invariant:
            # building the measurement is pure observability work (adapter lookup, prompt
            # estimation, context extraction). Every downstream path already handles
            # measurement is None, so degrade any measurement failure to "record nothing" and
            # still forward the real request, rather than letting it abort the proxied call.
            try:
                measurement = self._measurement(body, started_at=started_at)
            except Exception:  # noqa: BLE001 - a measurement failure must never break the proxied call
                measurement = None
            parsed_upstream, target_path = _merge_path(
                config.upstream_base_url or "",
                self.path,
            )
            connection_type = http.client.HTTPSConnection if parsed_upstream.scheme == "https" else http.client.HTTPConnection
            connection = connection_type(
                parsed_upstream.hostname,
                parsed_upstream.port,
                timeout=config.timeout_seconds,
            )
            response_started = False
            provider_request_id: str | None = None
            response_headers_ms: float | None = None
            try:
                connection.request(
                    self.command,
                    target_path,
                    body=body or None,
                    headers=self._upstream_headers(body),
                )
                upstream = connection.getresponse()
                response_headers_ms = (monotonic() - started_at) * 1000
                provider_request_id = _provider_request_id(upstream)
                content_type = upstream.getheader("Content-Type", "")
                response_started = True
                if content_type.lower().startswith("text/event-stream"):
                    self._stream_response(
                        upstream,
                        measurement,
                        provider_request_id=provider_request_id,
                        response_headers_ms=response_headers_ms,
                    )
                else:
                    self._buffered_response(
                        upstream,
                        measurement,
                        provider_request_id=provider_request_id,
                        response_headers_ms=response_headers_ms,
                    )
            except Exception as exc:  # noqa: BLE001 - return a stable gateway error
                if measurement is not None:
                    observation = _observation(
                        measurement,
                        status="failed",
                        authoritative=False,
                        streaming=False,
                        http_status=None,
                        provider_request_id=provider_request_id,
                        provider_response_id=None,
                        response_headers_ms=response_headers_ms,
                        time_to_first_token_ms=None,
                        duration_ms=(monotonic() - started_at) * 1000,
                        error_type=type(exc).__name__,
                    )
                    event = _estimate_only_event(
                        measurement,
                        response_hash=hashlib.sha256(b"").hexdigest(),
                        flags=["proxy_upstream_error"],
                        observation=observation,
                    )
                    self._persist(event)
                if not response_started and not self.wfile.closed:
                    try:
                        self._send_local(502, {"error": "upstream_unavailable"})
                    except OSError:
                        pass
            finally:
                connection.close()

        def _copy_response_headers(
            self,
            upstream: http.client.HTTPResponse,
            *,
            streaming: bool,
            body_length: int | None = None,
        ) -> None:
            self.send_response(upstream.status, upstream.reason)
            for key, value in upstream.getheaders():
                lowered = key.lower()
                if lowered in _HOP_BY_HOP or lowered == "content-length":
                    continue
                self.send_header(key, value)
            if streaming:
                self.send_header("Transfer-Encoding", "chunked")
            elif body_length is not None:
                self.send_header("Content-Length", str(body_length))
            self.end_headers()

        def _buffered_response(
            self,
            upstream: http.client.HTTPResponse,
            measurement: _RequestMeasurement | None,
            *,
            provider_request_id: str | None,
            response_headers_ms: float | None,
        ) -> None:
            response_body = upstream.read()
            if measurement is not None:
                response_hash = hashlib.sha256(response_body).hexdigest()
                try:
                    payload = json.loads(response_body)
                except (json.JSONDecodeError, UnicodeError):
                    payload = None
                provider_response_id = payload.get("id") if isinstance(payload, dict) and isinstance(payload.get("id"), str) else None
                duration_ms = (monotonic() - measurement.started_at) * 1000
                if isinstance(payload, dict):
                    successful = upstream.status < 400
                    observation = _observation(
                        measurement,
                        status="complete" if successful else "failed",
                        authoritative=successful,
                        streaming=False,
                        http_status=upstream.status,
                        provider_request_id=provider_request_id,
                        provider_response_id=provider_response_id,
                        response_headers_ms=response_headers_ms,
                        time_to_first_token_ms=None,
                        duration_ms=duration_ms,
                    )
                    event = normalize(
                        payload,
                        measurement.adapter,
                        context=measurement.context,
                        request_hash=measurement.request_hash,
                        response_hash=response_hash,
                        extra_flags=(["provider_http_error"] if upstream.status >= 400 else None),
                        observation=observation,
                    )
                    if event.quantities:
                        _response_comparison(event, measurement.estimate)
                    else:
                        observation = _observation(
                            measurement,
                            status=("failed" if upstream.status >= 400 else "incomplete"),
                            authoritative=False,
                            streaming=False,
                            http_status=upstream.status,
                            provider_request_id=provider_request_id,
                            provider_response_id=provider_response_id,
                            response_headers_ms=response_headers_ms,
                            time_to_first_token_ms=None,
                            duration_ms=duration_ms,
                        )
                        event = (
                            _estimate_only_event(
                                measurement,
                                response_hash=response_hash,
                                flags=[
                                    "provider_usage_missing",
                                    *(["provider_http_error"] if upstream.status >= 400 else []),
                                ],
                                observation=observation,
                            )
                            if upstream.status >= 400 or config.record_success_without_usage
                            else None
                        )
                else:
                    observation = _observation(
                        measurement,
                        status=("failed" if upstream.status >= 400 else "incomplete"),
                        authoritative=False,
                        streaming=False,
                        http_status=upstream.status,
                        provider_request_id=provider_request_id,
                        provider_response_id=None,
                        response_headers_ms=response_headers_ms,
                        time_to_first_token_ms=None,
                        duration_ms=duration_ms,
                    )
                    event = (
                        _estimate_only_event(
                            measurement,
                            response_hash=response_hash,
                            flags=[
                                "provider_response_unparseable",
                                *(["provider_http_error"] if upstream.status >= 400 else []),
                            ],
                            observation=observation,
                        )
                        if upstream.status >= 400 or config.record_success_without_usage
                        else None
                    )
                # Make the observation queryable before the caller sees a completed response.
                if event is not None:
                    self._persist(event)

            self._copy_response_headers(
                upstream,
                streaming=False,
                body_length=len(response_body),
            )
            self.wfile.write(response_body)

        def _stream_response(
            self,
            upstream: http.client.HTTPResponse,
            measurement: _RequestMeasurement | None,
            *,
            provider_request_id: str | None,
            response_headers_ms: float | None,
        ) -> None:
            self._copy_response_headers(upstream, streaming=True)
            accumulator = _UsageAccumulator(measurement.adapter) if measurement is not None else None
            parser = _SSEParser(accumulator) if accumulator is not None else None
            response_hasher = hashlib.sha256()
            downstream_open = True
            time_to_first_token_ms: float | None = None
            while True:
                chunk = upstream.read1(64 * 1024)
                if not chunk:
                    break
                response_hasher.update(chunk)
                if parser is not None:
                    parser.feed(chunk)
                    if measurement is not None and time_to_first_token_ms is None and parser.saw_output_delta:
                        time_to_first_token_ms = (monotonic() - measurement.started_at) * 1000
                if downstream_open:
                    try:
                        self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    except OSError:
                        downstream_open = False

            if parser is not None:
                parser.finish()
            if measurement is not None and accumulator is not None:
                response_hash = response_hasher.hexdigest()
                duration_ms = (monotonic() - measurement.started_at) * 1000
                # Truncated stream: EOF without the provider's terminal marker (message_stop /
                # [DONE] / response.completed). Whatever usage DID arrive is real and kept, but
                # the event must not pass for a complete one.
                truncated = parser is not None and not parser.saw_terminal
                terminal_usage_missing = not accumulator.saw_terminal_usage
                if truncated and accumulator.quantities:
                    if parser.saw_output_delta and not any(tt == TokenType.OUTPUT for tt, _role in accumulator.quantities):
                        # output was visibly streaming but its count never arrived: surface the
                        # loss as an UNKNOWN output (INV-6), never silently omit it.
                        additivity, subtotal_of = assign_additivity(
                            measurement.adapter.provider,
                            measurement.adapter.api_surface,
                            TokenType.OUTPUT,
                        )
                        accumulator.quantities[(TokenType.OUTPUT, None)] = TokenQuantity(
                            token_type=TokenType.OUTPUT,
                            quantity=None,
                            precision_level=PrecisionLevel.UNKNOWN,
                            usage_source=UsageSource.NONE,
                            additivity=additivity,
                            subtotal_of=subtotal_of,
                            unknown_reason=UnknownReason.STREAM_INTERRUPTED,
                        )
                    if "stream_interrupted" not in accumulator.flags:
                        accumulator.flags.append("stream_interrupted")
                if terminal_usage_missing and "provider_stream_usage_missing" not in accumulator.flags:
                    accumulator.flags.append("provider_stream_usage_missing")
                has_usage = bool(accumulator.quantities)
                successful = upstream.status < 400 and has_usage
                observation = _observation(
                    measurement,
                    status=(
                        ("incomplete" if truncated or terminal_usage_missing else "complete")
                        if successful
                        else ("failed" if upstream.status >= 400 else "incomplete")
                    ),
                    authoritative=successful,
                    streaming=True,
                    http_status=upstream.status,
                    provider_request_id=provider_request_id,
                    provider_response_id=accumulator.provider_response_id,
                    response_headers_ms=response_headers_ms,
                    time_to_first_token_ms=time_to_first_token_ms,
                    duration_ms=duration_ms,
                )
                observation.update(
                    usage_contract_observation(sorted(accumulator.unmapped_usage_fields))
                )
                event = accumulator.build_event(
                    context=measurement.context,
                    request_hash=measurement.request_hash,
                    response_hash=response_hash,
                    observation=observation,
                )
                if event is None:
                    event = (
                        _estimate_only_event(
                            measurement,
                            response_hash=response_hash,
                            flags=[
                                *accumulator.flags,
                                "provider_stream_usage_missing",
                                *(["provider_http_error"] if upstream.status >= 400 else []),
                            ],
                            observation=observation,
                        )
                        if upstream.status >= 400 or config.record_success_without_usage
                        else None
                    )
                else:
                    _response_comparison(event, measurement.estimate)
                if event is not None:
                    self._persist(event)

            if downstream_open:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass

    return _ProxyHandler


def create_proxy_server(
    repository: FileRepository,
    config: ProxyConfig,
    *,
    estimator: Estimator = estimate_prompt,
    on_event: ObservationCallback | None = None,
) -> ThreadingHTTPServer:
    """Build a loopback proxy server; use port 0 for an ephemeral test port."""

    class _QuietProxyServer(ThreadingHTTPServer):
        def handle_error(self, request: Any, client_address: Any) -> None:
            exc = sys.exc_info()[1]
            if isinstance(
                exc,
                (BrokenPipeError, ConnectionAbortedError, ConnectionResetError),
            ):
                return
            if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {
                10053,
                10054,
            }:
                return
            super().handle_error(request, client_address)

    server = _QuietProxyServer(
        (config.host, config.port),
        _make_handler(
            config,
            repository,
            estimator=estimator,
            on_event=on_event,
        ),
    )
    server.daemon_threads = True
    return server
