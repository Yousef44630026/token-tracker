"""Low-cost live Vertex AI proof harness for generation, streaming, and embeddings."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urlerr
from urllib import parse, request

from tracker.adapters.vertex_ai_embeddings_adapter import VertexAIEmbeddingsAdapter
from tracker.adapters.vertex_ai_generate_content_adapter import VertexAIGenerateContentAdapter
from tracker.context.propagation import new_trace
from tracker.models.enums import PrecisionLevel
from tracker.models.token_event import TokenEvent
from tracker.normalization.normalizer import normalize
from tracker.observability.observation import Observation
from tracker.ops.provider_proof import write_capture_attestation
from tracker.ops.runtime_fingerprint import runtime_fingerprint
from tracker.storage.file_repository import FileRepository
from tracker.streaming.sse import parse_sse_json
from tracker.streaming.stream_consumer import consume_stream

Opener = Callable[[request.Request, float], Any]
_SURFACES = frozenset({"generate", "stream", "embeddings"})
_LOCATION_RE = re.compile(r"^(?:global|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)$")


@dataclass(frozen=True)
class VertexProofResult:
    surface: str
    status: str
    detail: str
    http_status: int | None = None
    contributing_tokens: int = 0
    provider_total_tokens: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    event_id: str | None = None
    artifact: str | None = None


@dataclass(frozen=True)
class VertexProofSummary:
    passed: bool
    out_dir: str
    ran_count: int
    skipped_count: int
    failure_count: int
    event_count: int
    observed_total_contributing_tokens: int
    artifacts: dict[str, str]
    results: list[VertexProofResult]
    generated_at: str = ""
    runtime_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def _env(environment: Mapping[str, str], name: str) -> str | None:
    value = environment.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _access_token(environment: Mapping[str, str], *, allow_gcloud: bool) -> tuple[str | None, str]:
    supplied = _env(environment, "VERTEX_ACCESS_TOKEN")
    if supplied:
        return supplied, "environment"
    if not allow_gcloud:
        return None, "missing"
    try:
        completed = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "gcloud_unavailable"
    token = completed.stdout.strip() if completed.returncode == 0 else ""
    return (token, "gcloud") if token else (None, "gcloud_auth_failed")


def _service_endpoint(location: str) -> str:
    return "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"


def _model_url(project: str, location: str, model: str, method: str, *, sse: bool = False) -> str:
    resource = "/".join(
        parse.quote(part, safe="")
        for part in ("projects", project, "locations", location, "publishers", "google", "models", model)
    )
    url = f"https://{_service_endpoint(location)}/v1/{resource}:{method}"
    return f"{url}?alt=sse" if sse else url


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _audit_payload(value: Any) -> Any:
    """Keep proof structure while excluding large, analytically useless vectors."""
    if isinstance(value, Mapping):
        return {
            key: ({"redacted_vector_length": len(child)} if key == "values" and isinstance(child, list) else _audit_payload(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_audit_payload(child) for child in value]
    return value


def _stream_payloads(raw: bytes) -> list[dict[str, Any]]:
    if b"data:" in raw:
        return parse_sse_json(raw)
    decoded = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list) and all(isinstance(item, dict) for item in decoded):
        return list(decoded)
    raise ValueError("Vertex stream must be SSE, a JSON object, or an array of JSON objects")


def _candidate_text(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    fragments: list[str] = []
    for candidate in payload.get("candidates", []) or []:
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts", []) or []:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                fragments.append(part["text"])
    return "".join(fragments)


def _observation(surface: str, *, status: int, duration_ms: float, location: str, model: str) -> Observation:
    return Observation(
        authoritative=True,
        status="complete",
        http_status=status,
        duration_ms=round(duration_ms, 3),
        service_name="vertex-proof",
        cloud_provider="gcp",
        region=location,
        deployment=model,
        extra={"scenario": f"vertex-{surface}-proof"},
    )


def _is_exact_and_reconciled(event: TokenEvent) -> bool:
    known = [quantity for quantity in event.quantities if quantity.quantity is not None]
    return (
        bool(known)
        and all(quantity.precision_level == PrecisionLevel.EXACT for quantity in known)
        and event.provider_total_tokens is not None
        and event.event_total_mismatch == 0
        and event.over_attributed_tokens == 0
        and not {"raw_usage_missing", "provider_usage_missing", "provider_stream_usage_missing"}.intersection(
            event.data_quality_flags
        )
    )


def _call(
    *,
    surface: str,
    url: str,
    body: dict[str, Any],
    token: str,
    location: str,
    model: str,
    opener: Opener,
    timeout: float,
    out_dir: Path,
) -> tuple[VertexProofResult, TokenEvent | None]:
    req = request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200))
    except urlerr.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        artifact = _write_json(
            out_dir / "raw" / f"{surface}.json",
            {"status": "fail", "http_status": exc.code, "error": detail, "captured_at": _timestamp()},
        )
        return VertexProofResult(surface, "fail", f"provider_http_error:{exc.code}", exc.code, artifact=artifact), None
    except Exception as exc:  # noqa: BLE001 - live transport failures become bounded evidence
        artifact = _write_json(
            out_dir / "raw" / f"{surface}.json",
            {"status": "fail", "error": f"{type(exc).__name__}: {exc}", "captured_at": _timestamp()},
        )
        return VertexProofResult(surface, "fail", "network_or_client_failure", artifact=artifact), None

    duration_ms = (time.perf_counter() - started) * 1000
    context = new_trace(workflow="provider-proof", environment="validation", business_id="vertex")
    try:
        if surface == "stream":
            payloads = _stream_payloads(raw)
            event = consume_stream(
                payloads,
                VertexAIGenerateContentAdapter(model_id=model),
                context=context,
                text_extractor=_candidate_text,
                model=model,
            )
            stream_observation = _observation(
                surface,
                status=status,
                duration_ms=duration_ms,
                location=location,
                model=model,
            )
            event.observation.update(stream_observation.to_dict())
            raw_payload: Any = payloads
            response_text = "".join(_candidate_text(item) for item in payloads)
        else:
            payload = json.loads(raw.decode("utf-8", "replace"))
            adapter = (
                VertexAIEmbeddingsAdapter(model_id=model)
                if surface == "embeddings"
                else VertexAIGenerateContentAdapter(model_id=model)
            )
            event = normalize(
                payload,
                adapter,
                context=context,
                timestamp=_timestamp(),
                observation=_observation(
                    surface,
                    status=status,
                    duration_ms=duration_ms,
                    location=location,
                    model=model,
                ).to_dict(),
            )
            raw_payload = payload
            response_text = _candidate_text(payload) if surface == "generate" else "embedding"
    except Exception as exc:  # noqa: BLE001 - malformed provider evidence fails closed
        artifact = _write_json(
            out_dir / "raw" / f"{surface}.json",
            {
                "status": "fail",
                "http_status": status,
                "error": f"{type(exc).__name__}: {exc}",
                "captured_at": _timestamp(),
            },
        )
        return VertexProofResult(surface, "fail", "malformed_provider_response", status, artifact=artifact), None

    passed = _is_exact_and_reconciled(event) and bool(response_text.strip())
    detail = "normalized and reconciled" if passed else "usage_or_response_validation_failed"
    artifact = _write_json(
        out_dir / "raw" / f"{surface}.json",
        {
            "status": "pass" if passed else "fail",
            "http_status": status,
            "captured_at": _timestamp(),
            "response": _audit_payload(raw_payload),
        },
    )
    return (
        VertexProofResult(
            surface=surface,
            status="pass" if passed else "fail",
            detail=detail,
            http_status=status,
            contributing_tokens=event.event_contributing_tokens,
            provider_total_tokens=event.provider_total_tokens,
            data_quality_flags=list(event.data_quality_flags),
            event_id=event.event_id,
            artifact=artifact,
        ),
        event,
    )


def run_vertex_smoke(
    *,
    out_dir: str | None = None,
    environment: Mapping[str, str] | None = None,
    opener: Opener | None = None,
    timeout: float = 30.0,
    dry_run: bool = False,
    require_live: bool = False,
    surfaces: Sequence[str] | None = None,
) -> VertexProofSummary:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    selected = list(dict.fromkeys(surfaces or ("generate", "stream", "embeddings")))
    unknown = [surface for surface in selected if surface not in _SURFACES]
    if unknown:
        raise ValueError(f"unknown Vertex surfaces: {', '.join(unknown)}")
    env = dict(os.environ if environment is None else environment)
    generated_at = _timestamp()
    code_fingerprint = runtime_fingerprint()
    root = Path(out_dir or Path("runs") / "vertex-smoke" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    project = _env(env, "VERTEX_PROJECT_ID")
    location = _env(env, "VERTEX_LOCATION")
    generate_model = _env(env, "VERTEX_GENERATIVE_MODEL")
    embedding_model = _env(env, "VERTEX_EMBEDDING_MODEL")
    required_models_present = all(
        (embedding_model if surface == "embeddings" else generate_model)
        for surface in selected
    )
    allow_gcloud = bool(not dry_run and project and location and required_models_present)
    token, token_source = _access_token(env, allow_gcloud=allow_gcloud)
    artifacts = {
        "config": _write_json(
            root / "config_redacted.json",
            {
                "project_id": project,
                "location": location,
                "generative_model": generate_model,
                "embedding_model": embedding_model,
                "access_token": "present" if token else "missing",
                "access_token_source": token_source,
            },
        )
    }
    results: list[VertexProofResult] = []
    events: list[TokenEvent] = []
    for surface in selected:
        model = embedding_model if surface == "embeddings" else generate_model
        if location and not _LOCATION_RE.fullmatch(location):
            results.append(VertexProofResult(surface, "fail", "invalid VERTEX_LOCATION"))
            continue
        missing = [
            name
            for name, value in (
                ("VERTEX_PROJECT_ID", project),
                ("VERTEX_LOCATION", location),
                ("VERTEX_GENERATIVE_MODEL" if surface != "embeddings" else "VERTEX_EMBEDDING_MODEL", model),
                ("VERTEX_ACCESS_TOKEN or gcloud auth", token),
            )
            if not value
        ]
        if missing:
            results.append(VertexProofResult(surface, "skip", "missing configuration: " + ", ".join(missing)))
            continue
        if dry_run:
            results.append(VertexProofResult(surface, "skip", "dry-run: no live call executed"))
            continue
        assert project and location and model and token
        if surface == "embeddings":
            method = "embedContent"
            body = {
                "content": {"parts": [{"text": "Runbook de reprise du service Paiements"}]},
                "embedContentConfig": {"taskType": "RETRIEVAL_DOCUMENT", "outputDimensionality": 64},
            }
        else:
            method = "streamGenerateContent" if surface == "stream" else "generateContent"
            body = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Un systeme produit 9 lots de 14 unites et perd 17 unites. "
                                    "Donne le total restant et une justification courte."
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 128},
            }
        result, event = _call(
            surface=surface,
            url=_model_url(project, location, model, method, sse=surface == "stream"),
            body=body,
            token=token,
            location=location,
            model=model,
            opener=opener or request.urlopen,
            timeout=timeout,
            out_dir=root,
        )
        results.append(result)
        if event is not None:
            events.append(event)

    if events:
        repository = FileRepository(root / "events.jsonl")
        repository.append_many(events)
        artifacts["events_jsonl"] = str(repository.path)
    failures = sum(result.status == "fail" for result in results)
    ran = sum(result.status != "skip" for result in results)
    skipped = sum(result.status == "skip" for result in results)
    if require_live and ran == 0:
        failures += 1
    passed = failures == 0 and (ran > 0 or not require_live)
    summary = VertexProofSummary(
        passed=passed,
        out_dir=str(root),
        ran_count=ran,
        skipped_count=skipped,
        failure_count=failures,
        event_count=len(events),
        observed_total_contributing_tokens=sum(event.event_contributing_tokens for event in events),
        artifacts=artifacts,
        results=results,
        generated_at=generated_at,
        runtime_fingerprint=code_fingerprint,
    )
    artifacts["summary"] = _write_json(root / "summary.json", summary.to_dict())
    capture_key = _env(env, "TRACKER_PROOF_CAPTURE_KEY_FILE")
    if capture_key:
        artifacts["capture_attestation"] = write_capture_attestation(
            artifacts["summary"], capture_key, harness="vertex_smoke"
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove Vertex AI token extraction using live low-cost calls")
    parser.add_argument("--out-dir")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--surface", action="append", choices=sorted(_SURFACES), dest="surfaces")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = run_vertex_smoke(
        out_dir=args.out_dir,
        timeout=args.timeout,
        dry_run=args.dry_run,
        require_live=args.require_live,
        surfaces=args.surfaces,
    )
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Vertex AI token proof harness")
        for result in summary.results:
            print(
                f"[{result.status.upper()}] {result.surface}: {result.detail} | "
                f"tokens={result.contributing_tokens} provider_total={result.provider_total_tokens}"
            )
        print(
            f"summary: passed={summary.passed} ran={summary.ran_count} skipped={summary.skipped_count} "
            f"failures={summary.failure_count} tokens={summary.observed_total_contributing_tokens}"
        )
        print(f"artifacts: {summary.out_dir}")
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
