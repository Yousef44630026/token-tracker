"""Trust/reporting additions: observation contract, provider matrix, HTML report."""

import os
import shutil
import sys
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.analytics.observation_contract import (  # noqa: E402
    build_observation_contract_summary,
    validate_trace_observations,
)
from tracker.analytics.provider_validation import (  # noqa: E402
    build_provider_validation_matrix,
    fixture_record,
    matrix_to_markdown,
    summarize_provider_validation,
)
from tracker.export.html_report import export_html_report, render_html_report  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.observability.observation import Observation, build_observation  # noqa: E402
from tracker.proxy.cli import main as proxy_main  # noqa: E402
from tracker.validation.fixture_manifest import realistic_fixture_records  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def quantity(token_type, value):
    return TokenQuantity(
        token_type=token_type,
        quantity=value,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=Additivity.TOTAL_CONTRIBUTING,
    )


trace = Trace(trace_id="trust-trace", workflow="support", environment="prod")
valid_observation = build_observation(
    status="complete",
    authoritative=True,
    http_status=200,
    duration_ms=120.5,
    time_to_first_token_ms=30,
    provider_request_id="req-1",
    provider_response_id="resp-1",
    service_name="support-api",
    tenant_id="tenant-a",
    cloud_provider="azure",
    region="francecentral",
)
check(
    Observation(status="complete", http_status=200).to_dict()["status"] == "complete",
    "typed Observation serializes valid operational metadata",
)
bad_builder = False
try:
    build_observation(status="mystery")
except ValueError:
    bad_builder = True
check(bad_builder, "typed Observation rejects invalid status")

missing_authority_rejected = False
try:
    TokenEvent(
        event_id="missing-authority",
        request_correlation_id="req-missing-authority",
        trace_id=trace.trace_id,
        span_id="span-missing-authority",
        quantities=[quantity(TokenType.INPUT, 1)],
        observation={"status": "complete"},
    )
except ValueError:
    missing_authority_rejected = True
check(missing_authority_rejected, "TokenEvent requires explicit authoritative when observation metadata is present")

empty_observation_rejected = False
try:
    TokenEvent(
        event_id="empty-observation",
        request_correlation_id="req-empty-observation",
        trace_id=trace.trace_id,
        span_id="span-empty-observation",
        quantities=[quantity(TokenType.INPUT, 1)],
        observation={},
    )
except ValueError:
    empty_observation_rejected = True
check(empty_observation_rejected, "TokenEvent rejects observation={} instead of defaulting into totals")

typo_authority_rejected = False
try:
    TokenEvent(
        event_id="typo-authority",
        request_correlation_id="req-typo-authority",
        trace_id=trace.trace_id,
        span_id="span-typo-authority",
        quantities=[quantity(TokenType.INPUT, 1)],
        observation={"status": "failed", "authoratative": False},
    )
except ValueError:
    typo_authority_rejected = True
check(typo_authority_rejected, "TokenEvent rejects misspelled authoritative instead of defaulting into totals")

non_authoritative = TokenEvent(
    event_id="non-authoritative",
    request_correlation_id="req-non-authoritative",
    trace_id=trace.trace_id,
    span_id="span-non-authoritative",
    quantities=[quantity(TokenType.INPUT, 10)],
    observation=Observation(authoritative=False, status="failed"),
)
check(
    non_authoritative.is_authoritative is False and non_authoritative.event_contributing_tokens == 0,
    "typed Observation(authoritative=False) gates totals to zero",
)

trace.add_event(
    TokenEvent(
        event_id="valid",
        request_correlation_id="req-valid",
        trace_id=trace.trace_id,
        span_id="span-valid",
        provider="azure_openai",
        model="gpt-test",
        api_surface="responses",
        quantities=[quantity(TokenType.INPUT, 10), quantity(TokenType.OUTPUT, 5)],
        provider_total_tokens=15,
        observation=valid_observation,
    )
)
invalid = TokenEvent(
    event_id="invalid",
    request_correlation_id="req-invalid",
    trace_id=trace.trace_id,
    span_id="span-invalid",
    provider="bedrock",
    model="nova",
    api_surface="converse",
    quantities=[],
    data_quality_flags=["raw_usage_missing"],
    observation={"authoritative": True},
)
# Simulate a legacy/corrupt event object after load/mutation. New TokenEvent construction rejects
# this shape, but the observation-contract analytics should still diagnose it if encountered.
invalid.observation = {
    "status": "mystery",
    "authoritative": "yes",
    "http_status": 999,
    "duration_ms": -1,
    "retry_count": -2,
    "provider_request_id": "",
    "fallback_from": "bedrock",
}
trace.add_event(invalid)

issues = validate_trace_observations(trace)
codes = sorted(issue.code for issue in issues)
check("invalid_status" in codes, "observation contract flags invalid status")
check("invalid_boolean_field" in codes, "observation contract flags invalid authoritative")
check("invalid_http_status" in codes, "observation contract flags invalid HTTP status")
check("invalid_non_negative_number" in codes, "observation contract flags negative duration")
check("invalid_non_negative_integer" in codes, "observation contract flags negative retry count")
check("invalid_string_field" in codes, "observation contract flags empty provider_request_id")
check("incomplete_fallback_pair" in codes, "observation contract flags incomplete fallback pair")

summary = build_observation_contract_summary(trace)
check(summary["event_count"] == 2, "observation summary counts events")
check(summary["events_with_issues"] == 1, "observation summary counts affected events")
check(summary["issue_count"] == len(issues), "observation summary issue count matches details")

records = [
    fixture_record("azure_openai_responses.REAL.json", AzureOpenAIResponsesAdapter),
    fixture_record("azure_chat_content_filter.SIMULATED.json", AzureOpenAIChatCompletionsAdapter),
    fixture_record("bedrock_converse.REAL.json", BedrockConverseAdapter),
    fixture_record("bedrock_converse_cache.SIMULATED.json", BedrockConverseAdapter),
]
matrix = build_provider_validation_matrix(
    records,
    adapter_pairs=[
        ("azure_openai", "responses"),
        ("azure_openai", "chat_completions"),
        ("bedrock", "converse"),
        ("openai", "embeddings"),
    ],
)
rows = {(row["provider"], row["api_surface"]): row for row in matrix}
check(rows[("azure_openai", "responses")]["validation_level"] == "real_only", "matrix shows Azure Responses has real coverage")
check(rows[("azure_openai", "chat_completions")]["validation_level"] == "simulated_only", "matrix shows Azure Chat simulated-only coverage")
check(rows[("bedrock", "converse")]["validation_level"] == "real_and_simulated", "matrix shows Bedrock Converse real+simulated coverage")
check("cache_not_real_validated" in rows[("bedrock", "converse")]["gaps"], "matrix highlights simulated-only cache coverage")
check(rows[("openai", "embeddings")]["validation_level"] == "adapter_only", "matrix exposes adapter-only surfaces")
matrix_summary = summarize_provider_validation(matrix)
check(matrix_summary["overall_status"] == "fail", "matrix summary fails when adapter-only surfaces exist")
check(matrix_summary["fail_count"] == 1, "matrix summary counts failing surfaces")
check("| Status | Provider | Surface |" in matrix_to_markdown(matrix), "matrix renders to Markdown")
real_records = realistic_fixture_records()
check(
    any(record.fixture_name == "azure_openai_responses.REAL.json" for record in real_records),
    "central fixture manifest exposes real Azure Responses record",
)
real_matrix_summary = summarize_provider_validation(build_provider_validation_matrix(real_records))
check(real_matrix_summary["fail_count"] == 0, "central provider matrix has zero adapter-only failures")
check(real_matrix_summary["overall_status"] != "fail", "central provider matrix readiness is not failing")

html = render_html_report(trace, title="Trust Report")
for text in (
    "Trust Report",
    "Readiness Overview",
    "Trace Summary",
    "Observation Contract",
    "Provider Validation Matrix",
    "Service Attribution",
    "Anomalies",
):
    check(text in html, f"HTML report contains {text}")

out_dir = os.path.join(os.getcwd(), ".test_html_report_out")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, "report.html")
export_html_report(trace, path, title="Trust Report")
check(os.path.exists(path), "HTML report is written")
with open(path, encoding="utf-8") as handle:
    written = handle.read()
check("raw_usage_missing" in written, "HTML report includes anomaly signals")
shutil.rmtree(out_dir, ignore_errors=True)

buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = proxy_main(["provider-matrix"])
cli_output = buffer.getvalue()
check(exit_code == 0, "provider-matrix CLI exits successfully")
check("Provider validation readiness" in cli_output, "provider-matrix CLI renders readiness summary")
check("| Status | Provider | Surface |" in cli_output, "provider-matrix CLI renders markdown table")

out_dir = os.path.join(os.getcwd(), ".test_provider_matrix_out")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir, exist_ok=True)
matrix_path = os.path.join(out_dir, "provider_matrix.md")
buffer = StringIO()
with redirect_stdout(buffer):
    output_exit = proxy_main(["provider-matrix", "--output", matrix_path])
check(output_exit == 0 and os.path.exists(matrix_path), "provider-matrix CLI writes output artifact")
with open(matrix_path, encoding="utf-8") as handle:
    artifact = handle.read()
check("Provider validation readiness" in artifact, "provider-matrix artifact contains readiness summary")
shutil.rmtree(out_dir, ignore_errors=True)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
