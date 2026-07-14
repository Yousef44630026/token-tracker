"""Azure smoke harness tests with a fake HTTP opener (no live network calls)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from io import BytesIO
from pathlib import Path
from urllib import error as urlerr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.azure_smoke import classify_live_error, run_smoke  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()


class FakeResponse:
    def __init__(self, payload: dict, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {"apim-request-id": "req-unit"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def fake_opener(req, timeout):
    url = req.full_url
    assert "/openai/v1/openai/" not in url, url
    assert req.get_header("Api-key") == "secret-unit-key"
    assert req.get_header("Authorization") is None
    body = json.loads(req.data.decode("utf-8")) if req.data else {}
    if "/chat/completions" in url:
        return FakeResponse(
            {
                "id": "chatcmpl-unit",
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "bonjour"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        )
    if url.endswith("/responses"):
        assert body["max_output_tokens"] == 16, body
        return FakeResponse(
            {
                "id": "resp-unit",
                "model": "gpt-5-mini",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            }
        )
    if "/embeddings" in url:
        return FakeResponse(
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            }
        )
    raise AssertionError(f"unexpected URL: {url}")


def error_opener(req, timeout):
    body = BytesIO(b'{"error":{"code":"Unauthorized","message":"bad key"}}')
    raise urlerr.HTTPError(req.full_url, 401, "Unauthorized", hdrs={}, fp=body)


root = Path(os.path.abspath(f".test_azure_smoke_{uuid.uuid4().hex}"))
root.mkdir(parents=True, exist_ok=True)

env = {
    "AZURE_OPENAI_API_KEY": "secret-unit-key",
    "AZURE_OPENAI_ENDPOINT": "https://unit.openai.azure.com/openai/v1",
    "AZURE_OPENAI_DEPLOYMENT": "chat-dep",
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
    "AZURE_OPENAI_RESPONSES_ENDPOINT": "https://unit.openai.azure.com",
    "AZURE_OPENAI_RESPONSES_DEPLOYMENT": "resp-dep",
    "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT": "embed-dep",
    "AZURE_REGION": "unit-region",
}

summary = run_smoke(out_dir=str(root / "success"), environment=env, opener=fake_opener)
check(summary.passed is True, "all fake Azure surfaces pass")
check(summary.ran_count == 3, "three live-capable surfaces ran")
check(summary.skipped_count == 0, "no configured surface skipped")
check(summary.event_count == 3, "one event persisted per surface")
check(summary.observed_total_contributing_tokens == 24, "chat + responses + embeddings total reconciles")
check(Path(summary.artifacts["events_jsonl"]).exists(), "events JSONL is written")
check(Path(summary.artifacts["trust_report"]).exists(), "trust report is written")
check((Path(summary.artifacts["csv_dir"]) / "token_events.csv").exists(), "CSV bundle is written")
check(Path(summary.artifacts["excel"]).exists(), "Excel audit workbook is written")
check(Path(summary.artifacts["readme"]).exists(), "audit README is written")

events = FileRepository(summary.artifacts["events_jsonl"]).read_all()
check(all(event.provider == "azure_openai" for event in events), "events keep the Azure provider label")
check(all(event.is_authoritative for event in events), "successful live events are authoritative")
check(all(event.observation.get("deployment") for event in events), "deployment is carried in observation metadata")
check(all(event.observation.get("region") == "unit-region" for event in events), "region is carried from injected env")

config_text = Path(summary.artifacts["config"]).read_text(encoding="utf-8")
check("secret-unit-key" not in config_text, "redacted config never writes the API key")
check('"AZURE_OPENAI_API_KEY": "present"' in config_text, "redacted config records key presence only")
config_payload = json.loads(config_text)
check(
    config_payload["configured_profiles"] == ["foundry-responses", "azure-chat", "azure-embeddings"],
    "redacted config records configured Azure/Foundry profiles",
)

missing_summary = run_smoke(out_dir=str(root / "missing"), environment={}, opener=fake_opener)
check(missing_summary.passed is True, "missing env is a zero-cost skip by default")
check(missing_summary.ran_count == 0, "missing env runs no live calls")
check(missing_summary.skipped_count == 3, "missing env reports skipped surfaces")
check("events_jsonl" not in missing_summary.artifacts, "missing env writes no events")

required_summary = run_smoke(out_dir=str(root / "required"), environment={}, opener=fake_opener, require_live=True)
check(required_summary.passed is False, "require_live fails when nothing can run")
check(required_summary.failure_count == 1, "require_live records a failure")


def forbidden_opener(req, timeout):
    raise AssertionError("invalid credential formats must fail before network I/O")


connection_string_env = {
    "AZURE_OPENAI_API_KEY": "InstrumentationKey=unit;IngestionEndpoint=https://unit.invalid/",
    "AZURE_OPENAI_RESPONSES_ENDPOINT": "https://unit.services.ai.azure.com/api/projects/unit/openai/v1",
    "AZURE_OPENAI_RESPONSES_DEPLOYMENT": "gpt-unit",
}
connection_string_summary = run_smoke(
    out_dir=str(root / "connection-string"),
    environment=connection_string_env,
    opener=forbidden_opener,
    require_live=True,
)
check(connection_string_summary.ran_count == 0, "connection string is rejected before a live call")
check(connection_string_summary.failure_count == 1, "require_live fails an invalid credential format")
connection_string_result = next(result for result in connection_string_summary.results if result.case == "responses")
check(
    "looks like a connection string" in connection_string_result.detail,
    "credential format failure explains the operator mistake",
)

malformed_env = {
    "AZURE_OPENAI_API_KEY": "secret-unit-key",
    "AZURE_OPENAI_ENDPOINT": "https://unit.openai.azure.com/openai/v1",
    "AZURE_OPENAI_DEPLOYMENT": "                 >> gpt-5-mini",
}
malformed_summary = run_smoke(out_dir=str(root / "malformed"), environment=malformed_env, opener=fake_opener)
check(malformed_summary.ran_count == 0, "malformed deployment name prevents a misleading live call")
check(
    "whitespace or shell prompt markers" in malformed_summary.results[0].detail,
    "malformed deployment name is explained clearly",
)

error_env = {
    "AZURE_OPENAI_API_KEY": "bad-key",
    "AZURE_OPENAI_ENDPOINT": "https://unit.openai.azure.com",
    "AZURE_OPENAI_DEPLOYMENT": "chat-dep",
}
error_summary = run_smoke(out_dir=str(root / "error"), environment=error_env, opener=error_opener)
check(error_summary.passed is False, "HTTP failure makes smoke fail")
check(error_summary.failure_count == 1, "one HTTP failure counted")
error_events = FileRepository(error_summary.artifacts["events_jsonl"]).read_all()
check(error_events[0].is_authoritative is False, "HTTP error event is non-authoritative")
check(error_events[0].data_quality_flags == ["auth_failure"], "HTTP 401 is classified as auth_failure")

check(classify_live_error(404, "deployment not found") == "deployment_or_endpoint_not_found", "404 classification is stable")
check(classify_live_error(429, "quota") == "rate_limited_or_quota", "429 classification is stable")
check(classify_live_error(400, "content_filter blocked") == "content_filter", "content-filter classification is stable")

sys.exit(check.report("RESULT test_azure_smoke_harness"))
