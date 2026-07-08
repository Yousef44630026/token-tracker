"""Live Azure OpenAI smoke harness with audit artifacts.

The harness is intentionally tiny-cost by default: it runs only one short request per
configured surface, captures the raw response/error, normalizes TokenEvents, and writes an
audit bundle that can be reviewed without re-running live traffic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urlerr
from urllib import parse, request

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter
from tracker.analytics.trust_report import build_trust_report
from tracker.context.model import TraceContext, new_trace
from tracker.export.csv_exporter import export_csv
from tracker.export.excel_exporter import export_excel
from tracker.models.token_event import TokenEvent
from tracker.models.trace import Trace
from tracker.normalization.normalizer import normalize
from tracker.observability.observation import Observation
from tracker.storage.file_repository import FileRepository

Opener = Callable[[request.Request, float], Any]


@dataclass(frozen=True)
class AzureSmokeCase:
    """One live Azure call to attempt."""

    name: str
    surface: str
    deployment: str
    endpoint: str
    body: dict[str, Any]
    api_version: str | None = None


@dataclass(frozen=True)
class AzureSmokeResult:
    """One smoke case outcome."""

    case: str
    surface: str
    status: str
    detail: str
    http_status: int | None = None
    event_id: str | None = None
    contributing_tokens: int = 0
    provider_total_tokens: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    artifact: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def skipped(self) -> bool:
        return self.status == "skip"


@dataclass(frozen=True)
class AzureSmokeSummary:
    """Whole-run result."""

    out_dir: str
    passed: bool
    ran_count: int
    skipped_count: int
    failure_count: int
    event_count: int
    observed_total_contributing_tokens: int
    artifacts: dict[str, str]
    results: list[AzureSmokeResult]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results"] = [asdict(result) for result in self.results]
        return data


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_timestamp_for_path() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _env(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resource_endpoint(endpoint: str) -> str:
    """Return the Azure OpenAI resource endpoint used by deployment routes."""
    trimmed = endpoint.rstrip("/")
    suffix = "/openai/v1"
    if trimmed.lower().endswith(suffix):
        return trimmed[: -len(suffix)]
    return trimmed


def _responses_endpoint(endpoint: str) -> str:
    """Return the Azure OpenAI v1 endpoint used by the Responses route."""
    trimmed = endpoint.rstrip("/")
    if trimmed.lower().endswith("/openai/v1"):
        return trimmed
    return f"{trimmed}/openai/v1"


def _deployment_error(value: str | None) -> str | None:
    if not value:
        return None
    if any(char.isspace() for char in value) or ">" in value or "<" in value:
        return "deployment name contains whitespace or shell prompt markers"
    return None


def _missing(environment: Mapping[str, str], keys: Sequence[str]) -> list[str]:
    return [key for key in keys if not _env(environment, key)]


def _redacted_config(environment: Mapping[str, str]) -> dict[str, Any]:
    keys = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_RESPONSES_ENDPOINT",
        "AZURE_OPENAI_RESPONSES_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",
        "AZURE_REGION",
    )
    data = {key: _env(environment, key) for key in keys if _env(environment, key)}
    data["AZURE_OPENAI_API_KEY"] = "present" if _env(environment, "AZURE_OPENAI_API_KEY") else "missing"
    return data


def planned_cases(environment: Mapping[str, str]) -> tuple[list[AzureSmokeCase], list[AzureSmokeResult]]:
    """Build runnable cases from env vars and skip records for missing optional surfaces."""
    api_key_missing = _missing(environment, ("AZURE_OPENAI_API_KEY",))
    skips: list[AzureSmokeResult] = []
    cases: list[AzureSmokeCase] = []
    api_version = _env(environment, "AZURE_OPENAI_API_VERSION") or "2024-10-21"

    chat_missing = api_key_missing + _missing(environment, ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"))
    if chat_missing:
        skips.append(_skip("chat", "chat_completions", chat_missing))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_DEPLOYMENT")):
        skips.append(AzureSmokeResult("chat", "chat_completions", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="chat",
                surface="chat_completions",
                endpoint=_resource_endpoint(_env(environment, "AZURE_OPENAI_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_DEPLOYMENT") or "",
                api_version=api_version,
                body={
                    "messages": [{"role": "user", "content": "Reponds en un seul mot: bonjour"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
        )

    responses_missing = api_key_missing + _missing(
        environment,
        ("AZURE_OPENAI_RESPONSES_ENDPOINT", "AZURE_OPENAI_RESPONSES_DEPLOYMENT"),
    )
    if responses_missing:
        skips.append(_skip("responses", "responses", responses_missing))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT")):
        skips.append(AzureSmokeResult("responses", "responses", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="responses",
                surface="responses",
                endpoint=_responses_endpoint(_env(environment, "AZURE_OPENAI_RESPONSES_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT") or "",
                body={
                    "model": _env(environment, "AZURE_OPENAI_RESPONSES_DEPLOYMENT"),
                    "input": "Reponds en un seul mot: bonjour",
                    "max_output_tokens": 16,
                },
            )
        )

    embeddings_missing = api_key_missing + _missing(
        environment,
        ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"),
    )
    if embeddings_missing:
        skips.append(_skip("embeddings", "embeddings", embeddings_missing))
    elif error := _deployment_error(_env(environment, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT")):
        skips.append(AzureSmokeResult("embeddings", "embeddings", "skip", error))
    else:
        cases.append(
            AzureSmokeCase(
                name="embeddings",
                surface="embeddings",
                endpoint=_resource_endpoint(_env(environment, "AZURE_OPENAI_ENDPOINT") or ""),
                deployment=_env(environment, "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT") or "",
                api_version=api_version,
                body={"input": "bonjour"},
            )
        )
    return cases, skips


def _skip(case: str, surface: str, missing: Sequence[str]) -> AzureSmokeResult:
    return AzureSmokeResult(
        case=case,
        surface=surface,
        status="skip",
        detail="missing env vars: " + ", ".join(dict.fromkeys(missing)),
    )


def _case_url(case: AzureSmokeCase) -> str:
    endpoint = case.endpoint.rstrip("/")
    deployment = parse.quote(case.deployment, safe="")
    if case.surface == "responses":
        return f"{endpoint}/responses"
    if case.surface == "embeddings":
        return f"{endpoint}/openai/deployments/{deployment}/embeddings?api-version={case.api_version}"
    return f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={case.api_version}"


def _adapter_for(case: AzureSmokeCase):
    if case.surface == "responses":
        return AzureOpenAIResponsesAdapter(deployment=case.deployment)
    if case.surface == "embeddings":
        return AzureOpenAIEmbeddingsAdapter(deployment=case.deployment)
    return AzureOpenAIChatCompletionsAdapter(deployment=case.deployment)


def _request_for(case: AzureSmokeCase, api_key: str) -> request.Request:
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
        "Authorization": f"Bearer {api_key}",
    }
    return request.Request(
        _case_url(case),
        data=json.dumps(case.body).encode("utf-8"),
        method="POST",
        headers=headers,
    )


def classify_live_error(status: int | None, detail: str) -> str:
    """Return a stable, low-cardinality failure label for Azure live calls."""
    text = detail.lower()
    if status in {401, 403}:
        return "auth_failure"
    if status == 404:
        return "deployment_or_endpoint_not_found"
    if status == 408 or "timed out" in text or "timeout" in text:
        return "timeout"
    if status == 429:
        return "rate_limited_or_quota"
    if status == 400 and "content_filter" in text:
        return "content_filter"
    if status is not None:
        return "provider_http_error"
    if "name or service not known" in text or "getaddrinfo" in text:
        return "dns_failure"
    return "network_or_client_failure"


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _response_header(headers: Any, *names: str) -> str | None:
    for name in names:
        try:
            value = headers.get(name)
        except AttributeError:
            value = None
        if value:
            return str(value)
    return None


def _provider_response_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("id")
    return str(value) if value else None


def _normalize_success(
    case: AzureSmokeCase,
    payload: dict[str, Any],
    ctx: TraceContext,
    *,
    http_status: int,
    duration_ms: float,
    headers: Any,
    region: str | None,
) -> TokenEvent:
    observation = Observation(
        authoritative=True,
        status="complete",
        http_status=http_status,
        duration_ms=round(duration_ms, 3),
        provider_request_id=_response_header(headers, "apim-request-id", "x-ms-request-id", "request-id", "x-request-id"),
        provider_response_id=_provider_response_id(payload),
        service_name="azure-smoke",
        cloud_provider="azure",
        region=region,
        deployment=case.deployment,
    )
    return normalize(
        payload,
        _adapter_for(case),
        context=ctx,
        timestamp=_timestamp(),
        observation=observation.to_dict(),
    )


def _error_event(
    case: AzureSmokeCase,
    ctx: TraceContext,
    *,
    status: int | None,
    duration_ms: float,
    error_code: str,
    detail: str,
    region: str | None,
) -> TokenEvent:
    return TokenEvent(
        event_id=f"azure-smoke-{case.name}-{ctx.request_correlation_id}",
        request_correlation_id=ctx.request_correlation_id,
        trace_id=ctx.trace_id,
        span_id=ctx.span_id,
        parent_span_id=ctx.parent_span_id,
        workflow=ctx.workflow,
        environment=ctx.environment,
        provider="azure_openai",
        api_surface=case.surface,
        quantities=[],
        provider_total_tokens=None,
        data_quality_flags=[error_code],
        timestamp=_timestamp(),
        observation=Observation(
            authoritative=False,
            status="failed",
            http_status=status,
            duration_ms=round(duration_ms, 3),
            provider_error_code=error_code,
            service_name="azure-smoke",
            cloud_provider="azure",
            region=region,
            deployment=case.deployment,
            extra={"failure_detail": detail[:500]},
        ),
    )


def _run_case(
    case: AzureSmokeCase,
    *,
    api_key: str,
    out_dir: Path,
    opener: Opener,
    timeout: float,
    root_context: TraceContext,
    region: str | None,
) -> tuple[AzureSmokeResult, TokenEvent | None]:
    ctx = root_context.child_span()
    raw_path = out_dir / "raw" / f"{case.name}.json"
    req = _request_for(case, api_key)
    started = time.perf_counter()
    try:
        with opener(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            duration_ms = (time.perf_counter() - started) * 1000
            payload = json.loads(body)
            http_status = int(getattr(response, "status", 200))
            headers = getattr(response, "headers", {})
    except urlerr.HTTPError as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        detail = exc.read().decode("utf-8", "replace")
        error_code = classify_live_error(exc.code, detail)
        event = _error_event(case, ctx, status=exc.code, duration_ms=duration_ms, error_code=error_code, detail=detail, region=region)
        artifact = _write_json(
            raw_path,
            {
                "case": asdict(case),
                "captured_at": _timestamp(),
                "status": "fail",
                "http_status": exc.code,
                "error_code": error_code,
                "error_detail": detail[:2000],
            },
        )
        return (
            AzureSmokeResult(case.name, case.surface, "fail", error_code, exc.code, event.event_id, artifact=artifact),
            event,
        )
    except Exception as exc:  # noqa: BLE001 - classify live failures into the report
        duration_ms = (time.perf_counter() - started) * 1000
        detail = f"{type(exc).__name__}: {exc}"
        error_code = classify_live_error(None, detail)
        event = _error_event(case, ctx, status=None, duration_ms=duration_ms, error_code=error_code, detail=detail, region=region)
        artifact = _write_json(
            raw_path,
            {
                "case": asdict(case),
                "captured_at": _timestamp(),
                "status": "fail",
                "error_code": error_code,
                "error_detail": detail,
            },
        )
        return (
            AzureSmokeResult(case.name, case.surface, "fail", error_code, None, event.event_id, artifact=artifact),
            event,
        )

    event = _normalize_success(case, payload, ctx, http_status=http_status, duration_ms=duration_ms, headers=headers, region=region)
    artifact = _write_json(
        raw_path,
        {
            "case": asdict(case),
            "captured_at": _timestamp(),
            "status": "pass",
            "http_status": http_status,
            "response": payload,
        },
    )
    mismatch = event.event_total_mismatch
    failed = mismatch not in (None, 0) or bool(event.over_attributed_tokens)
    status = "fail" if failed else "pass"
    detail = "normalized and reconciled" if not failed else f"normalization mismatch={mismatch}"
    return (
        AzureSmokeResult(
            case=case.name,
            surface=case.surface,
            status=status,
            detail=detail,
            http_status=http_status,
            event_id=event.event_id,
            contributing_tokens=event.event_contributing_tokens,
            provider_total_tokens=event.provider_total_tokens,
            data_quality_flags=list(event.data_quality_flags),
            artifact=artifact,
        ),
        event,
    )


def _write_audit_readme(path: Path, summary: AzureSmokeSummary) -> str:
    lines = [
        "# Azure Smoke Audit Bundle",
        "",
        f"- passed: {summary.passed}",
        f"- ran_count: {summary.ran_count}",
        f"- skipped_count: {summary.skipped_count}",
        f"- failure_count: {summary.failure_count}",
        f"- event_count: {summary.event_count}",
        f"- observed_total_contributing_tokens: {summary.observed_total_contributing_tokens}",
        "",
        "## Cases",
        "",
    ]
    for result in summary.results:
        lines.append(
            f"- {result.case} ({result.surface}): {result.status} - {result.detail}; "
            f"tokens={result.contributing_tokens}; flags={result.data_quality_flags or '-'}"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
        ]
    )
    for name, artifact in summary.artifacts.items():
        lines.append(f"- {name}: `{artifact}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_smoke(
    *,
    out_dir: str | None = None,
    environment: Mapping[str, str] | None = None,
    opener: Opener | None = None,
    timeout: float = 30.0,
    dry_run: bool = False,
    require_live: bool = False,
) -> AzureSmokeSummary:
    """Run live Azure smoke cases and write an audit bundle."""
    env = dict(os.environ if environment is None else environment)
    root = Path(out_dir or Path("runs") / "azure-smoke" / _safe_timestamp_for_path()).resolve()
    cases, skip_results = planned_cases(env)
    artifacts: dict[str, str] = {}
    root.mkdir(parents=True, exist_ok=True)
    artifacts["config"] = _write_json(root / "config_redacted.json", _redacted_config(env))
    artifacts["plan"] = _write_json(
        root / "plan.json",
        {"cases": [asdict(case) for case in cases], "skips": [asdict(s) for s in skip_results]},
    )

    if dry_run:
        results = [AzureSmokeResult(case.name, case.surface, "skip", "dry-run: no live call executed") for case in cases] + skip_results
        summary = AzureSmokeSummary(
            out_dir=str(root),
            passed=not require_live,
            ran_count=0,
            skipped_count=len(results),
            failure_count=1 if require_live else 0,
            event_count=0,
            observed_total_contributing_tokens=0,
            artifacts=artifacts,
            results=results,
        )
        artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
        artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
        return summary

    api_key = _env(env, "AZURE_OPENAI_API_KEY")
    http_opener = opener or request.urlopen
    root_context = new_trace(workflow="azure-smoke", environment="live")
    region = _env(env, "AZURE_REGION")
    events: list[TokenEvent] = []
    results: list[AzureSmokeResult] = []
    for case in cases:
        if not api_key:
            continue
        result, event = _run_case(
            case,
            api_key=api_key,
            out_dir=root,
            opener=http_opener,
            timeout=timeout,
            root_context=root_context,
            region=region,
        )
        results.append(result)
        if event is not None:
            events.append(event)
    results.extend(skip_results)

    if events:
        store_path = root / "events.jsonl"
        FileRepository(str(store_path)).append_many(events)
        artifacts["events_jsonl"] = str(store_path)
        trace = Trace(trace_id=root_context.trace_id, workflow="azure-smoke", environment="live", events=events)
        csv_dir = root / "csv"
        export_csv(trace, str(csv_dir))
        artifacts["csv_dir"] = str(csv_dir)
        excel_path = root / "azure_smoke.xlsx"
        export_excel(trace, str(excel_path))
        artifacts["excel"] = str(excel_path)
        trust_report = build_trust_report(trace).to_dict()
        artifacts["trust_report"] = _write_json(root / "trust_report.json", trust_report)
        observed_total = trust_report["observed_total_contributing_tokens"]
    else:
        observed_total = 0

    ran_count = sum(1 for result in results if not result.skipped)
    failure_count = sum(1 for result in results if result.failed)
    if require_live and ran_count == 0:
        failure_count += 1
    summary = AzureSmokeSummary(
        out_dir=str(root),
        passed=failure_count == 0,
        ran_count=ran_count,
        skipped_count=sum(1 for result in results if result.skipped),
        failure_count=failure_count,
        event_count=len(events),
        observed_total_contributing_tokens=observed_total,
        artifacts=artifacts,
        results=results,
    )
    artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
    artifacts["readme"] = _write_audit_readme(root / "README_AUDIT.md", summary)
    return summary


def _render_text(summary: AzureSmokeSummary) -> str:
    lines = ["Azure OpenAI smoke harness"]
    for result in summary.results:
        lines.append(f"[{result.status.upper()}] {result.case}: {result.detail}")
    lines.append(
        "summary: "
        f"passed={summary.passed} ran={summary.ran_count} skipped={summary.skipped_count} "
        f"failures={summary.failure_count} tokens={summary.observed_total_contributing_tokens}"
    )
    lines.append(f"artifacts: {summary.out_dir}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a tiny live Azure OpenAI smoke test and write an audit bundle")
    parser.add_argument("--out-dir", help="audit bundle directory; default runs/azure-smoke/<timestamp>")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true", help="write plan/config only; no live calls")
    parser.add_argument("--require-live", action="store_true", help="return non-zero if no live surface can run")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary = run_smoke(
        out_dir=args.out_dir,
        timeout=args.timeout,
        dry_run=args.dry_run,
        require_live=args.require_live,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) if args.json else _render_text(summary))
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
