"""Real-call proxy integration without credentials or paid provider traffic."""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlreq
from urllib.error import HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import PrecisionLevel, TokenType  # noqa: E402
from tracker.proxy.cli import _parser  # noqa: E402
from tracker.proxy.estimator import (  # noqa: E402
    PromptEstimate,
    estimate_prompt,
    extract_latest_user_text,
)
from tracker.proxy.report import summarize_events  # noqa: E402
from tracker.proxy.server import ProxyConfig, create_proxy_server  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
received = []


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


class FakeProvider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            request_payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeError):
            request_payload = {}
        received.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "x_api_key": self.headers.get("x-api-key"),
                "body": body,
            }
        )
        if self.path.startswith("/v1/messages"):
            if request_payload.get("model") == "error-test":
                payload = {"type": "error", "error": {"type": "rate_limit_error"}}
                data = json.dumps(payload).encode()
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("request-id", "req-anthropic-error")
                self.end_headers()
                self.wfile.write(data)
                return
            if request_payload.get("model") == "startup-probe":
                payload = {
                    "id": "probe_test",
                    "model": "startup-probe",
                    "content": [],
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if request_payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.send_header("request-id", "req-anthropic-stream")
                self.end_headers()
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_stream_test",
                            "model": "claude-stream-test",
                            "usage": {
                                "input_tokens": 15,
                                "output_tokens": 1,
                                "cache_read_input_tokens": 40,
                                "cache_creation_input_tokens": 30,
                                "cache_creation": {
                                    "ephemeral_5m_input_tokens": 20,
                                    "ephemeral_1h_input_tokens": 10,
                                },
                            },
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "stream answer"},
                    },
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 8},
                    },
                    {"type": "message_stop"},
                ]
                for event in events:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                self.close_connection = True
                return
            payload = {
                "id": "msg_test",
                "model": "claude-test",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 6,
                    "cache_creation": {
                        "ephemeral_5m_input_tokens": 6,
                        "ephemeral_1h_input_tokens": 0,
                    },
                },
            }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("request-id", "req-anthropic-json")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.send_header("x-request-id", "req-openai-stream")
        self.end_headers()
        events = [
            {"type": "response.output_text.delta", "delta": "hello"},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_test",
                    "model": "gpt-test",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 5,
                        "total_tokens": 25,
                        "input_tokens_details": {"cached_tokens": 4},
                        "output_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            },
        ]
        for event in events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def fixed_estimator(body, provider, surface):
    quantity = 9 if provider == "anthropic" else 18
    return PromptEstimate(
        quantity=quantity,
        estimator="tokentap_cl100k_base_test",
        text_characters=123,
        text_sha256="a" * 64,
    )


def post(url, payload, headers):
    body = json.dumps(payload).encode()
    request = urlreq.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urlreq.urlopen(request, timeout=5) as response:
        return response.status, response.read(), response.headers


# Configuration and estimator contract checks before opening any sockets.
try:
    ProxyConfig(provider="openai", host="0.0.0.0")
except ValueError:
    loopback_rejected = True
else:
    loopback_rejected = False
check(loopback_rejected, "proxy rejects non-loopback binding")

parsed = _parser().parse_args(["run", "--provider", "anthropic", "--store", "events.jsonl", "--", "claude"])
check(
    parsed.mode == "run" and parsed.provider == "anthropic" and parsed.command == ["--", "claude"],
    "documented run subcommand parses correctly",
)
report_args = _parser().parse_args(["report", "--store", "events.jsonl", "--json"])
check(
    report_args.mode == "report" and report_args.store == "events.jsonl" and report_args.json is True,
    "report subcommand parses correctly",
)

estimate = estimate_prompt(
    {"instructions": "system", "input": "hello"},
    "openai",
    "responses",
    counter=len,
    estimator_name="characters_for_test",
)
check(
    estimate.quantity == len("system\nhello") and estimate.estimator == "characters_for_test",
    "Responses input and instructions are included in the estimate",
)
latest_user = extract_latest_user_text(
    {
        "messages": [
            {"role": "user", "content": "human prompt"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1"}]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "tool output",
                    }
                ],
            },
        ]
    },
    "anthropic",
    "messages",
)
check(
    latest_user == "human prompt",
    "prompt attribution ignores tool-result payloads",
)

provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
provider.daemon_threads = True
threading.Thread(target=provider.serve_forever, daemon=True).start()
upstream = f"http://127.0.0.1:{provider.server_address[1]}"
anthropic_path = os.path.join(os.getcwd(), ".test_real_call_proxy_anthropic.jsonl")
openai_path = os.path.join(os.getcwd(), ".test_real_call_proxy_openai.jsonl")
for scratch_path in (anthropic_path, openai_path):
    with open(scratch_path, "w", encoding="utf-8"):
        pass
anthropic_proxy = None
openai_proxy = None

try:
    anthropic_repo = FileRepository(anthropic_path)
    anthropic_proxy = create_proxy_server(
        anthropic_repo,
        ProxyConfig(provider="anthropic", upstream_base_url=upstream, port=0),
        estimator=fixed_estimator,
    )
    threading.Thread(target=anthropic_proxy.serve_forever, daemon=True).start()
    anthropic_url = f"http://127.0.0.1:{anthropic_proxy.server_address[1]}"
    secret_prompt = "SENSITIVE_PROMPT_MARKER"
    probe_authorization = "Bearer " + "SECRET_" + "PROBE_TOKEN"
    error_authorization = "Bearer " + "SECRET_" + "ERROR_TOKEN"
    claude_authorization = "Bearer " + "SECRET_" + "CLAUDE_OAUTH"
    openai_authorization = "Bearer " + "SECRET_" + "OPENAI_TOKEN"

    status, body, _ = post(
        anthropic_url + "/v1/messages?beta=true",
        {
            "model": "claude-test",
            "system": "system",
            "messages": [{"role": "user", "content": secret_prompt}],
        },
        {"x-api-key": "SECRET_ANTHROPIC_KEY"},
    )
    check(status == 200 and json.loads(body)["id"] == "msg_test", "Anthropic response passes through")
    check(received[-1]["x_api_key"] == "SECRET_ANTHROPIC_KEY", "credential reaches fake upstream")
    check(received[-1]["path"] == "/v1/messages?beta=true", "path and query pass through")

    events = anthropic_repo.read_all()
    check(len(events) == 1, "Anthropic call persists one event")
    event = events[0]
    input_q = next(q for q in event.quantities if q.token_type == TokenType.INPUT)
    output_q = next(q for q in event.quantities if q.token_type == TokenType.OUTPUT)
    comparison = input_q.metadata["prompt_estimate"]
    check(input_q.quantity == 11 and input_q.precision_level == PrecisionLevel.EXACT, "provider input is exact")
    check(output_q.quantity == 7 and event.event_contributing_tokens == 28, "provider output and all input buckets are exact")
    check(
        comparison["quantity"] == 9 and comparison["provider_prompt_tokens"] == 21 and comparison["provider_minus_estimate"] == 12,
        "estimate compares against fresh + cached + created prompt tokens",
    )
    check(event.timestamp and event.timestamp.endswith("Z"), "proxy event receives a UTC timestamp")
    check(
        event.observation["status"] == "complete"
        and event.observation["authoritative"] is True
        and event.observation["http_status"] == 200,
        "complete response stores authoritative HTTP observation",
    )
    check(
        event.observation["provider_request_id"] == "req-anthropic-json" and event.observation["provider_response_id"] == "msg_test",
        "provider request and response ids are captured",
    )
    check(
        event.observation["request_sequence"] == 1 and event.observation["proxy_session_id"].startswith("proxy-"),
        "proxy session and request sequence are captured",
    )
    check(
        len(event.observation["prompt_fingerprint"]) == 64
        and event.observation["prompt_sequence"] == 1
        and event.observation["prompt_cycle"] == 1,
        "human prompt is attributed by hash, sequence, and cycle",
    )
    cache_creation_q = next(q for q in event.quantities if q.token_type == TokenType.CACHE_CREATION_INPUT)
    check(
        cache_creation_q.metadata["ephemeral_5m_input_tokens"] == 6 and cache_creation_q.metadata["ephemeral_1h_input_tokens"] == 0,
        "Anthropic 5m/1h cache creation detail is retained",
    )

    with open(anthropic_repo.path, encoding="utf-8") as handle:
        stored = handle.read()
    check("SECRET_ANTHROPIC_KEY" not in stored, "credential is never persisted")
    check(secret_prompt not in stored, "raw prompt is never persisted")

    status, body, _ = post(
        anthropic_url + "/v1/messages",
        {
            "model": "startup-probe",
            "messages": [{"role": "user", "content": "probe"}],
        },
        {"Authorization": probe_authorization},
    )
    check(status == 200 and json.loads(body)["id"] == "probe_test", "successful no-usage probe passes through")
    check(len(anthropic_repo.read_all()) == 1, "successful startup/no-usage traffic is not persisted")

    try:
        post(
            anthropic_url + "/v1/messages",
            {
                "model": "error-test",
                "messages": [{"role": "user", "content": "ERROR_SECRET_PROMPT"}],
            },
            {"Authorization": error_authorization},
        )
    except HTTPError as exc:
        check(exc.code == 429, "provider HTTP error passes through with its status")
    else:
        check(False, "provider HTTP error should raise HTTPError in the test client")
    error_event = anthropic_repo.read_all()[-1]
    check(
        error_event.observation["status"] == "failed"
        and error_event.observation["authoritative"] is False
        and error_event.event_contributing_tokens == 0,
        "failed provider call is preserved but excluded from authoritative totals",
    )
    check(
        error_event.observation["provider_request_id"] == "req-anthropic-error" and "provider_http_error" in error_event.data_quality_flags,
        "failed call retains request id and HTTP error flag",
    )

    status, body, headers = post(
        anthropic_url + "/v1/messages",
        {
            "model": "claude-stream-test",
            "messages": [{"role": "user", "content": "STREAM_SECRET_PROMPT"}],
            "stream": True,
        },
        {"Authorization": claude_authorization},
    )
    check(status == 200 and b"message_delta" in body, "Anthropic SSE passes through")
    check(headers.get_content_type() == "text/event-stream", "Anthropic SSE content type is preserved")
    events = anthropic_repo.read_all()
    check(len(events) == 3, "Anthropic stream persists after the failed observation")
    stream_event = events[-1]
    stream_by_type = {q.token_type: q for q in stream_event.quantities}
    check(stream_by_type[TokenType.INPUT].quantity == 15, "Anthropic split stream input is exact")
    check(stream_by_type[TokenType.OUTPUT].quantity == 8, "Anthropic split stream output is exact")
    check(stream_by_type[TokenType.CACHED_INPUT].quantity == 40, "Anthropic cache read is exact")
    check(stream_by_type[TokenType.CACHE_CREATION_INPUT].quantity == 30, "Anthropic cache creation is exact")
    check(stream_event.event_contributing_tokens == 93, "Anthropic split stream total includes every bucket")
    stream_comparison = stream_by_type[TokenType.INPUT].metadata["prompt_estimate"]
    check(
        stream_comparison["provider_prompt_tokens"] == 85 and stream_comparison["provider_minus_estimate"] == 76,
        "stream estimate compares against all exact prompt buckets",
    )
    check(
        stream_event.observation["provider_request_id"] == "req-anthropic-stream"
        and stream_event.observation["provider_response_id"] == "msg_stream_test"
        and stream_event.observation["time_to_first_token_ms"] is not None,
        "Anthropic stream captures ids and time to first output token",
    )
    stream_cache_creation = stream_by_type[TokenType.CACHE_CREATION_INPUT]
    check(
        stream_cache_creation.metadata["ephemeral_5m_input_tokens"] == 20
        and stream_cache_creation.metadata["ephemeral_1h_input_tokens"] == 10,
        "stream retains Anthropic cache lifetime breakdown",
    )

    with open(anthropic_repo.path, encoding="utf-8") as handle:
        stored = handle.read()
    check("SECRET_CLAUDE_OAUTH" not in stored, "Claude OAuth token is never persisted")
    check("STREAM_SECRET_PROMPT" not in stored, "streaming raw prompt is never persisted")

    openai_repo = FileRepository(openai_path)
    openai_proxy = create_proxy_server(
        openai_repo,
        ProxyConfig(provider="openai", upstream_base_url=upstream, port=0),
        estimator=fixed_estimator,
    )
    threading.Thread(target=openai_proxy.serve_forever, daemon=True).start()
    openai_url = f"http://127.0.0.1:{openai_proxy.server_address[1]}"
    status, body, headers = post(
        openai_url + "/v1/responses",
        {"model": "gpt-test", "input": "SENSITIVE_OPENAI_PROMPT", "stream": True},
        {"Authorization": openai_authorization},
    )
    check(status == 200 and b"response.completed" in body, "OpenAI SSE passes through")
    check(headers.get_content_type() == "text/event-stream", "SSE content type is preserved")
    check(received[-1]["authorization"] == openai_authorization, "OpenAI auth reaches fake upstream")

    events = openai_repo.read_all()
    check(len(events) == 1, "OpenAI stream persists one final event")
    event = events[0]
    by_type = {q.token_type: q for q in event.quantities}
    check(by_type[TokenType.INPUT].quantity == 20, "OpenAI stream exact input extracted")
    check(by_type[TokenType.OUTPUT].quantity == 5, "OpenAI stream exact output extracted")
    check(event.provider_total_tokens == 25 and event.event_total_mismatch == 0, "OpenAI total reconciles")
    check(by_type[TokenType.CACHED_INPUT].quantity == 4, "cached subtotal is retained")
    check(by_type[TokenType.REASONING].quantity == 3, "reasoning subtotal is retained")
    comparison = by_type[TokenType.INPUT].metadata["prompt_estimate"]
    check(comparison["quantity"] == 18 and comparison["provider_minus_estimate"] == 2, "stream estimate comparison is attached")
    check(
        event.observation["provider_request_id"] == "req-openai-stream"
        and event.observation["provider_response_id"] == "resp_test"
        and event.observation["duration_ms"] >= 0,
        "OpenAI stream captures provider ids and duration",
    )

    summary = summarize_events([*anthropic_repo.read_all(), *openai_repo.read_all()])
    check(
        summary["events"] == 4 and summary["exact_usage_events"] == 3 and summary["incomplete_events"] == 1,
        "reliability report separates complete and failed observations",
    )
    check(
        summary["provider_prompt_tokens"] == 126 and summary["estimated_prompt_tokens"] == 36,
        "reliability report aggregates prompt comparisons",
    )
    check(
        summary["contributing_tokens"] == 146
        and summary["stored_contributing_tokens"] == 146
        and summary["incomplete_estimated_tokens"] == 0
        and summary["legacy_rule_events"] == 0,
        "reliability report aggregates corrected contributing totals",
    )
    check(
        summary["statuses"] == {"complete": 3, "failed": 1}
        and summary["proxy_sessions"] == 2
        and summary["events_with_provider_request_id"] == 4
        and summary["events_with_provider_response_id"] == 3,
        "reliability report summarizes status, sessions, and provider ids",
    )
    check(
        summary["distinct_prompt_fingerprints"] == 4
        and summary["max_prompt_cycle"] == 1
        and summary["cache_creation_5m_input_tokens"] == 26
        and summary["cache_creation_1h_input_tokens"] == 10,
        "report summarizes prompt attribution and Anthropic cache lifetimes",
    )

    with open(openai_repo.path, encoding="utf-8") as handle:
        stored = handle.read()
    check("SECRET_OPENAI_TOKEN" not in stored, "OpenAI credential is never persisted")
    check("SENSITIVE_OPENAI_PROMPT" not in stored, "OpenAI raw prompt is never persisted")

finally:
    if anthropic_proxy is not None:
        anthropic_proxy.shutdown()
        anthropic_proxy.server_close()
    if openai_proxy is not None:
        openai_proxy.shutdown()
        openai_proxy.server_close()
    provider.shutdown()
    provider.server_close()
    for scratch_path in (anthropic_path, openai_path):
        try:
            os.remove(scratch_path)
        except OSError:
            pass

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
