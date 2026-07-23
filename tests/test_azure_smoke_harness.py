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
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.ops.azure_smoke import classify_live_error, run_smoke  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
COLLECTOR_TOKEN = "collector-" + "unit-token"


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {"apim-request-id": "req-unit"}
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.raw if self.raw is not None else json.dumps(self.payload).encode("utf-8")


class FakeCollector:
    def __init__(self) -> None:
        self.events: list[TokenEvent] = []

    def __call__(self, req, timeout):
        assert timeout > 0
        assert req.get_header("Authorization") == f"Bearer {COLLECTOR_TOKEN}"
        if req.get_method() == "POST":
            payload = json.loads(req.data.decode("utf-8"))
            incoming = [TokenEvent.from_dict(item, require_explicit_authority=True) for item in payload]
            known = {event.event_id for event in self.events}
            persisted = [event for event in incoming if event.event_id not in known]
            self.events.extend(persisted)
            return FakeResponse(
                {
                    "acked": [event.event_id for event in incoming],
                    "persisted": [event.event_id for event in persisted],
                    "rejected": 0,
                }
            )

        traces: dict[str, int] = {}
        total = 0
        for event in self.events:
            contributing = event.event_contributing_tokens
            total += contributing
            traces[event.trace_id] = traces.get(event.trace_id, 0) + contributing
        summary = {
            "events": len(self.events),
            "effective_events": len(self.events),
            "superseded_events": 0,
            "total": total,
        }
        if "summary=1" not in req.full_url:
            summary["traces"] = traces
        return FakeResponse(summary)


def fake_opener(req, timeout):
    url = req.full_url
    assert "/openai/v1/openai/" not in url, url
    assert req.get_header("Api-key") == "secret-unit-key"
    assert req.get_header("Authorization") is None
    body = json.loads(req.data.decode("utf-8")) if req.data else {}
    if "/chat/completions" in url:
        if body.get("model"):
            assert body["max_completion_tokens"] == 2048, body
            assert body["reasoning_effort"] == "minimal", body
            assert body["verbosity"] == "low", body
        return FakeResponse(
            {
                "id": "chatcmpl-unit",
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "bonjour"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        )
    if url.endswith("/responses"):
        if body.get("stream") is True:
            terminal = {
                "id": "resp-stream-unit",
                "model": "gpt-5-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Il faut cinq instances pour conserver la marge apres une panne.",
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 18, "output_tokens": 12, "total_tokens": 30},
            }
            stream_events = [
                {"type": "response.created", "response": {"id": "resp-stream-unit", "status": "in_progress"}},
                {"type": "response.output_text.delta", "delta": "Il faut cinq instances"},
                {"type": "response.completed", "response": terminal},
            ]
            raw = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in stream_events).encode(
                "utf-8"
            )
            return FakeResponse(raw=raw)
        format_name = body.get("text", {}).get("format", {}).get("name")
        if format_name == "optimization_verification":
            assert body.get("previous_response_id") == "resp-math-1", body
        elif format_name == "optimization_sensitivity":
            assert body.get("previous_response_id") == "resp-math-2", body
        elif format_name == "integer_optimization_solution":
            assert "previous_response_id" not in body, body
        if format_name == "grounded_incident_answer":
            output = {
                "incident_id": "INC-204",
                "severity": "SEV-2",
                "owner": "FinOps",
                "next_action": "desactiver le nouveau routage",
            }
        elif format_name == "risk_assessment":
            assert body["max_output_tokens"] == 512, body
            assert body["text"]["verbosity"] == "low", body
            assert "12 mots maximum" in body["input"], body
            output = {
                "risk_level": "high",
                "policy_violation": True,
                "recommended_control": "bloquer l'export et demander une approbation",
            }
        elif format_name == "integer_optimization_solution":
            output = {"x": 10, "y": 20, "max_profit": 1000, "optimal": True, "explanation": "vertex optimum"}
        elif format_name == "optimization_verification":
            output = {
                "feasible": True,
                "resource_1_used": 40,
                "resource_2_used": 50,
                "profit": 1000,
                "optimal": True,
            }
        elif format_name == "optimization_sensitivity":
            output = {"x": 20, "y": 0, "max_profit": 1400, "changed_optimum": True}
        else:
            output = None
        if body.get("tools"):
            response_output = [
                {
                    "type": "function_call",
                    "name": "lookup_runbook",
                    "arguments": '{"service":"Paiements","symptom":"HTTP 503","severity":"SEV-2"}',
                }
            ]
        elif output is not None:
            response_output = [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ]
        else:
            assert body["max_output_tokens"] == 128, body
            assert body["reasoning"] == {"effort": "low"}, body
            response_output = []
        response_id = {
            "integer_optimization_solution": "resp-math-1",
            "optimization_verification": "resp-math-2",
            "optimization_sensitivity": "resp-math-3",
        }.get(format_name, "resp-unit")
        return FakeResponse(
            {
                "id": response_id,
                "model": "gpt-5-mini",
                "status": "completed",
                "output": response_output,
                "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            }
        )
    if "/embeddings" in url:
        if url.endswith("/openai/v1/embeddings"):
            assert body["model"] == "embed-dep", body
        input_count = len(body["input"]) if isinstance(body.get("input"), list) else 1
        return FakeResponse(
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [0.1, 0.2]}
                    for index in range(input_count)
                ],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            }
        )
    raise AssertionError(f"unexpected URL: {url}")


def incomplete_opener(req, timeout):
    body = json.loads(req.data.decode("utf-8"))
    assert body["max_output_tokens"] == 128
    return FakeResponse(
        {
            "id": "resp-incomplete-unit",
            "model": "gpt-5-mini",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "reasoning", "content": []}],
            "usage": {"input_tokens": 16, "output_tokens": 0, "total_tokens": 16},
        }
    )


def wrong_math_opener(req, timeout):
    response = fake_opener(req, timeout)
    body = json.loads(req.data.decode("utf-8")) if req.data else {}
    if body.get("text", {}).get("format", {}).get("name") == "integer_optimization_solution":
        response.payload["output"][0]["content"][0]["text"] = json.dumps(
            {"x": 9, "y": 20, "max_profit": 960, "optimal": True, "explanation": "incorrect"}
        )
    return response


def stream_without_terminal_opener(req, timeout):
    body = json.loads(req.data.decode("utf-8")) if req.data else {}
    if body.get("stream") is True:
        partial = {"type": "response.output_text.delta", "delta": "reponse interrompue"}
        raw = f"event: {partial['type']}\ndata: {json.dumps(partial)}\n\n".encode()
        return FakeResponse(raw=raw)
    return fake_opener(req, timeout)


def error_opener(req, timeout):
    body = BytesIO(b'{"error":{"code":"Unauthorized","message":"bad key"}}')
    raise urlerr.HTTPError(req.full_url, 401, "Unauthorized", hdrs={}, fp=body)


def chat_length_opener(req, timeout):
    return FakeResponse(
        {
            "id": "chatcmpl-length-unit",
            "model": "gpt-5-mini",
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 90,
                "completion_tokens": 256,
                "completion_tokens_details": {"reasoning_tokens": 256},
                "total_tokens": 346,
            },
        }
    )


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

demo_collector = FakeCollector()
demo_env = {
    "AZURE_OPENAI_API_KEY": "secret-unit-key",
    "AZURE_OPENAI_RESPONSES_ENDPOINT": "https://unit.openai.azure.com",
    "AZURE_OPENAI_RESPONSES_DEPLOYMENT": "resp-dep",
    "TRACKER_AUTH_TOKEN": COLLECTOR_TOKEN,
}
demo_summary = run_smoke(
    out_dir=str(root / "collector-demo"),
    environment=demo_env,
    opener=fake_opener,
    collector_opener=demo_collector,
    surfaces=["responses"],
    collector_url="http://127.0.0.1:8787",
    require_live=True,
)
check(demo_summary.passed is True, "selected Foundry Responses demo passes end to end")
check(demo_summary.ran_count == 1 and demo_summary.skipped_count == 0, "demo runs only the selected surface")
check(demo_summary.observed_total_contributing_tokens == 7, "demo reports the exact provider token total")
check(demo_summary.collector_status == "published", "demo confirms collector publication")
check(demo_summary.collector_persisted_event_count == 1, "collector persists the demo event")
check(demo_summary.collector_trace_tokens == 7, "collector trace reread proves the same token total")
check(demo_summary.collector_total_after == 7, "collector total includes the newly published event")
check(demo_summary.trace_id == demo_collector.events[0].trace_id, "reported trace id identifies the stored event")
demo_artifacts = "".join(
    Path(path).read_text(encoding="utf-8")
    for path in demo_summary.artifacts.values()
    if Path(path).is_file() and Path(path).suffix in {".json", ".jsonl", ".md"}
)
check(COLLECTOR_TOKEN not in demo_artifacts, "collector bearer is absent from every text audit artifact")

suite_collector = FakeCollector()
suite_summary = run_smoke(
    out_dir=str(root / "multi-service-demo"),
    environment=demo_env,
    opener=fake_opener,
    collector_opener=suite_collector,
    collector_url="http://127.0.0.1:8787",
    require_live=True,
    suite="demo",
)
check(suite_summary.passed is True, "realistic demo suite passes every available surface")
check(suite_summary.suite == "demo", "audit summary identifies the demo suite")
check(suite_summary.ran_count == 8, "Responses configuration enables seven scenarios plus Foundry v1 Chat")
check(suite_summary.skipped_count == 1, "missing embedding deployment remains an explicit skip")
embedding_skip = next(result for result in suite_summary.results if result.case == "embeddings")
check(
    embedding_skip.detail == "missing env vars: AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",
    "Foundry v1 demo asks only for the missing embedding deployment",
)
check(suite_summary.event_count == 8, "one independently auditable event is emitted per live scenario")
check(suite_summary.observed_total_contributing_tokens == 84, "all demo scenario token totals reconcile")
check(suite_summary.collector_persisted_event_count == 8, "collector persists all successful demo scenarios")
check(suite_summary.collector_trace_tokens == 84, "collector trace total equals the demo provider totals")
suite_services = {event.observation.get("service_name") for event in suite_collector.events}
check(
    suite_services
    == {
        "azure-demo-rag",
        "azure-demo-agent",
        "azure-demo-governance",
        "azure-demo-math",
        "azure-demo-operations",
        "azure-demo-capacity",
    },
    "dashboard-facing service attribution distinguishes every demo workload",
)
check(
    all(event.observation.get("use_case") for event in suite_collector.events),
    "each demo event carries a concrete reporting use case",
)
stream_event = next(
    event
    for event in suite_collector.events
    if event.observation.get("use_case") == "streamed_capacity_reasoning"
)
check(stream_event.event_total_mismatch == 0, "Responses SSE usage reconciles to the provider total")
check(
    {quantity.usage_source.value for quantity in stream_event.quantities} == {"provider_stream_final"},
    "Responses SSE quantities retain provider_stream_final provenance",
)
check(stream_event.observation.get("stream_event_count") == 3, "stream audit records the observed SSE event count")
stream_result = next(result for result in suite_summary.results if result.case == "responses-stream-capacity-decision")
check("streamed, normalized and reconciled" in stream_result.detail, "demo summary identifies verified streaming")
stream_artifact = json.loads(Path(stream_result.artifact).read_text(encoding="utf-8"))
check(len(stream_artifact["stream_events"]) == 3, "audit artifact preserves the complete SSE event sequence")
math_events = [event for event in suite_collector.events if event.observation.get("service_name") == "azure-demo-math"]
check(len(math_events) == 3, "math problem and both follow-ups are independently metered")
math_results = [result for result in suite_summary.results if result.case.startswith("responses-math-")]
check(all("answer=" in result.detail for result in math_results), "terminal summary displays every verified math answer")
check(
    [event.observation.get("conversation_step") for event in math_events] == [1, 2, 3],
    "math follow-up sequence remains explicit and ordered",
)
check(
    len({event.observation.get("conversation_id") for event in math_events}) == 1,
    "all math calls retain one conversation identifier",
)
check(
    [event.observation.get("provider_previous_response_id") for event in math_events]
    == [None, "resp-math-1", "resp-math-2"],
    "each intelligent follow-up chains from the immediately preceding provider response",
)
wrong_math_summary = run_smoke(
    out_dir=str(root / "wrong-math-demo"),
    environment=demo_env,
    opener=wrong_math_opener,
    surfaces=["responses"],
    require_live=True,
    suite="demo",
)
wrong_math_result = next(result for result in wrong_math_summary.results if result.case == "responses-math-optimization")
check(wrong_math_summary.passed is False, "a mathematically incorrect structured answer fails the live demo")
check("incorrect JSON values" in wrong_math_result.detail, "math failure names the incorrect expected values")
check(wrong_math_result.contributing_tokens == 7, "a wrong answer still preserves its exact billed usage")
missing_terminal_summary = run_smoke(
    out_dir=str(root / "missing-stream-terminal-demo"),
    environment=demo_env,
    opener=stream_without_terminal_opener,
    surfaces=["responses"],
    require_live=True,
    suite="demo",
)
missing_terminal_result = next(
    result for result in missing_terminal_summary.results if result.case == "responses-stream-capacity-decision"
)
check(missing_terminal_summary.passed is False, "a Responses stream without a terminal usage event fails closed")
check(
    missing_terminal_result.detail == "stream proof failed: terminal response usage was not observed",
    "missing terminal stream evidence has an operator-readable failure",
)
check(
    "provider_stream_usage_missing" in missing_terminal_result.data_quality_flags,
    "interrupted Responses stream preserves the canonical missing-usage flag",
)
suite_plan = json.loads(Path(suite_summary.artifacts["plan"]).read_text(encoding="utf-8"))
check(suite_plan["suite"] == "demo", "audit plan records the selected suite")
check(
    any(case["profile"] == "foundry-chat-v1" for case in suite_plan["cases"]),
    "Foundry Responses credentials safely enable the compatible v1 Chat route",
)

v1_embedding_env = {**demo_env, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT": "embed-dep"}
v1_embedding_summary = run_smoke(
    out_dir=str(root / "foundry-v1-embeddings"),
    environment=v1_embedding_env,
    opener=fake_opener,
    suite="demo",
)
check(v1_embedding_summary.passed is True, "Foundry v1 batch embeddings pass without a classic Azure endpoint")
check(v1_embedding_summary.ran_count == 9, "embedding deployment enables the ninth realistic scenario")
check(v1_embedding_summary.skipped_count == 0, "all demo surfaces run when the embedding deployment exists")
embedding_event = next(
    event
    for event in FileRepository(v1_embedding_summary.artifacts["events_jsonl"]).read_all()
    if event.api_surface == "embeddings"
)
check(embedding_event.event_contributing_tokens == 5, "batch embedding provider usage is counted once as input")
check(
    embedding_event.observation.get("service_name") == "azure-demo-embeddings",
    "batch embeddings are attributed to the RAG indexing service",
)

incomplete_collector = FakeCollector()
incomplete_summary = run_smoke(
    out_dir=str(root / "collector-incomplete"),
    environment=demo_env,
    opener=incomplete_opener,
    collector_opener=incomplete_collector,
    surfaces=["responses"],
    collector_url="http://127.0.0.1:8787",
    require_live=True,
)
incomplete_result = incomplete_summary.results[0]
check(incomplete_summary.passed is False, "HTTP 200 with an incomplete provider response fails the smoke")
check(incomplete_result.contributing_tokens == 16, "incomplete calls still count exact consumed tokens")
check(
    "provider_response_incomplete" in incomplete_result.data_quality_flags,
    "incomplete provider state is audit-visible",
)
check(incomplete_summary.collector_status == "published", "incomplete billed usage is still published to the ledger")
check(incomplete_summary.collector_trace_tokens == 16, "collector preserves the incomplete call's exact usage")
check(incomplete_collector.events[0].observation.get("status") == "incomplete", "stored observation keeps provider status")
check(
    incomplete_collector.events[0].observation.get("provider_incomplete_reason") == "max_output_tokens",
    "stored observation keeps the bounded incomplete reason",
)

chat_length_env = {
    "AZURE_OPENAI_API_KEY": "secret-unit-key",
    "AZURE_OPENAI_ENDPOINT": "https://unit.openai.azure.com",
    "AZURE_OPENAI_DEPLOYMENT": "chat-dep",
}
chat_length_summary = run_smoke(
    out_dir=str(root / "chat-length"),
    environment=chat_length_env,
    opener=chat_length_opener,
    surfaces=["chat"],
)
chat_length_result = chat_length_summary.results[0]
chat_length_event = FileRepository(chat_length_summary.artifacts["events_jsonl"]).read_all()[0]
check(chat_length_summary.passed is False, "Chat finish_reason=length fails instead of masquerading as complete")
check(chat_length_result.contributing_tokens == 346, "truncated Chat still preserves exact billed usage")
check(
    "provider_response_incomplete" in chat_length_result.data_quality_flags,
    "Chat length truncation raises the canonical incomplete flag",
)
check(chat_length_event.observation.get("status") == "incomplete", "Chat length truncation is stored as incomplete")
check(
    chat_length_event.observation.get("provider_incomplete_reason") == "length",
    "Chat observation stores the bounded finish reason",
)
chat_length_artifact = json.loads(Path(chat_length_result.artifact).read_text(encoding="utf-8"))
check(chat_length_artifact["status"] == "fail", "raw audit status agrees with scenario validation")
check("provider_response_incomplete" in chat_length_artifact["detail"], "raw audit explains the failed validation")

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
