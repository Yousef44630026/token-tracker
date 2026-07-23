"""Strict delivery gate for provider proof and dashboard evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tracker.analytics.provider_validation import (
    build_capability_certification_matrix,
    build_provider_validation_matrix,
    certification_requirement_failures,
)
from tracker.ops.provider_proof import ProofValidation, proof_manifest_paths, validate_provider_proof
from tracker.ops.runtime_fingerprint import runtime_fingerprint
from tracker.validation.fixture_manifest import PROVIDER_CAPABILITY_POLICIES, realistic_fixture_records


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def warned(self) -> bool:
        return self.status == "warn"


def _check(name: str, status: str, detail: str, **data: Any) -> ReleaseCheck:
    return ReleaseCheck(name=name, status=status, detail=detail, data=data)


def _parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def provider_proof_checks(
    requirements: Sequence[str],
    *,
    reviewed_capabilities: Sequence[str] = (),
) -> list[ReleaseCheck]:
    records = realistic_fixture_records()
    surfaces = build_provider_validation_matrix(records)
    capabilities = build_capability_certification_matrix(records, PROVIDER_CAPABILITY_POLICIES)
    try:
        failures = certification_requirement_failures(surfaces, capabilities, requirements)
    except ValueError as exc:
        return [_check("provider-proof", "fail", str(exc))]
    failed_by_requirement = {failure.split("=", 1)[0]: failure.split("=", 1)[1] for failure in failures}
    externally_proven = set(reviewed_capabilities)
    return [
        _check(
            f"provider-proof:{requirement}",
            "fail" if requirement in failed_by_requirement and requirement not in externally_proven else "pass",
            (
                "reviewed live proof satisfies the required capability"
                if requirement in externally_proven
                else
                f"REAL proof required; current certification={failed_by_requirement[requirement]}"
                if requirement in failed_by_requirement
                else "REAL fixture proves the required surface or capability"
            ),
            requirement=requirement,
            certification=(
                "reviewed_live"
                if requirement in externally_proven
                else failed_by_requirement.get(requirement, "proven")
            ),
        )
        for requirement in requirements
    ]


def provider_proof_manifest_checks(
    directory: str | os.PathLike[str] | None,
    *,
    max_age_seconds: float,
    capture_key_file: str | None = None,
    review_key_file: str | None = None,
    now: dt.datetime | None = None,
) -> tuple[list[ReleaseCheck], list[ProofValidation]]:
    try:
        paths = proof_manifest_paths(directory)
    except ValueError as exc:
        return [_check("provider-proof-manifests", "fail", str(exc))], []
    checks: list[ReleaseCheck] = []
    validations: list[ProofValidation] = []
    if paths and (not capture_key_file or not review_key_file):
        return [
            _check(
                "provider-proof-keys",
                "fail",
                "reviewed provider proofs exist but capture/review verification keys are not configured",
            )
        ], []
    assert capture_key_file is not None or not paths
    assert review_key_file is not None or not paths
    for path in paths:
        validation = validate_provider_proof(
            path,
            max_age_seconds=max_age_seconds,
            capture_key_file=capture_key_file,
            review_key_file=review_key_file,
            now=now,
        )
        validations.append(validation)
        checks.append(
            _check(
                f"provider-proof-manifest:{path.stem}",
                "pass" if validation.valid else "fail",
                validation.detail,
                path=validation.path,
                proof_id=validation.proof_id,
                capabilities=list(validation.capabilities),
            )
        )
    return checks, validations


def dashboard_evidence_checks(
    path: str | os.PathLike[str],
    *,
    max_age_seconds: float,
    min_pricing_coverage: float | None = None,
    min_latency_coverage: float | None = None,
    min_instrumented_latency_coverage: float | None = None,
    required_quality_status: str | None = None,
    now: dt.datetime | None = None,
) -> list[ReleaseCheck]:
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            _check(
                "dashboard-evidence",
                "fail",
                f"dashboard evidence is unreadable: {type(exc).__name__}: {exc}",
                path=str(target),
            )
        ]
    if not isinstance(payload, dict):
        return [_check("dashboard-evidence", "fail", "dashboard evidence must be a JSON object", path=str(target))]

    checks: list[ReleaseCheck] = []
    status = payload.get("status")
    checks.append(
        _check(
            "dashboard-status",
            "pass" if status == "ok" and payload.get("exit_code") == 0 else "fail",
            "latest dashboard refresh succeeded" if status == "ok" else f"latest dashboard status={status!r}",
            path=str(target),
            observed_status=status,
            exit_code=payload.get("exit_code"),
        )
    )
    timestamp = _parse_timestamp(payload.get("timestamp"))
    current = now or dt.datetime.now(dt.UTC)
    if timestamp is None:
        checks.append(_check("dashboard-freshness", "fail", "dashboard evidence timestamp is missing or invalid"))
    else:
        age = max((current.astimezone(dt.UTC) - timestamp).total_seconds(), 0.0)
        checks.append(
            _check(
                "dashboard-freshness",
                "pass" if age <= max_age_seconds else "fail",
                f"dashboard evidence is {age:.0f}s old (limit {max_age_seconds:g}s)",
                age_seconds=age,
                max_age_seconds=max_age_seconds,
            )
        )

    report = payload.get("report")
    if not isinstance(report, dict):
        checks.append(_check("dashboard-report", "fail", "dashboard evidence contains no structured report"))
        return checks

    def coverage_check(name: str, key: str, minimum: float | None) -> None:
        if minimum is None:
            return
        value = report.get(key)
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        checks.append(
            _check(
                name,
                "pass" if valid and float(value) >= minimum else "fail",
                (
                    f"{key}={float(value):.1%} meets {minimum:.1%}"
                    if valid and float(value) >= minimum
                    else f"{key}={value!r}; required minimum={minimum:.1%}"
                ),
                observed=value,
                required_minimum=minimum,
            )
        )

    coverage_check("dashboard-pricing-coverage", "pricing_coverage", min_pricing_coverage)
    coverage_check("dashboard-latency-coverage", "latency_coverage", min_latency_coverage)
    coverage_check(
        "dashboard-instrumented-latency-coverage",
        "instrumented_latency_coverage",
        min_instrumented_latency_coverage,
    )
    if required_quality_status is not None:
        observed = report.get("quality_status")
        checks.append(
            _check(
                "dashboard-quality-status",
                "pass" if observed == required_quality_status else "fail",
                f"quality_status={observed!r}; required={required_quality_status!r}",
                observed=observed,
                required=required_quality_status,
            )
        )
    volume = report.get("volume_status")
    checks.append(
        _check(
            "dashboard-volume",
            "warn" if volume == "warning" else ("pass" if volume == "ok" else "fail"),
            f"volume_status={volume!r}; data_rows={report.get('data_row_count')!r}",
            volume_status=volume,
            data_row_count=report.get("data_row_count"),
        )
    )
    return checks


def scale_evidence_checks(
    path: str | os.PathLike[str],
    *,
    max_age_seconds: float,
    min_event_count: int,
    now: dt.datetime | None = None,
) -> list[ReleaseCheck]:
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            _check(
                "scale-evidence",
                "fail",
                f"scale evidence is unreadable: {type(exc).__name__}: {exc}",
                path=str(target),
            )
        ]
    if not isinstance(payload, dict):
        return [_check("scale-evidence", "fail", "scale evidence must be a JSON object", path=str(target))]

    checks: list[ReleaseCheck] = []
    passed = payload.get("passed") is True and not payload.get("failures")
    checks.append(
        _check(
            "scale-status",
            "pass" if passed else "fail",
            "scale probe passed its declared time and memory budgets" if passed else "scale probe did not pass",
            path=str(target),
            failures=payload.get("failures"),
        )
    )
    count = payload.get("event_count")
    effective = payload.get("effective_event_count")
    valid_count = isinstance(count, int) and not isinstance(count, bool) and count >= min_event_count
    reconciled = valid_count and effective == count and isinstance(payload.get("total_tokens"), int)
    checks.append(
        _check(
            "scale-volume",
            "pass" if reconciled else "fail",
            (
                f"scale evidence reconciles {count} events"
                if reconciled
                else f"event_count={count!r}, effective_event_count={effective!r}; required minimum={min_event_count}"
            ),
            observed=count,
            effective=effective,
            required_minimum=min_event_count,
        )
    )
    timestamp = _parse_timestamp(payload.get("generated_at"))
    current = now or dt.datetime.now(dt.UTC)
    if timestamp is None:
        checks.append(_check("scale-freshness", "fail", "scale evidence timestamp is missing or invalid"))
    else:
        age = max((current.astimezone(dt.UTC) - timestamp).total_seconds(), 0.0)
        checks.append(
            _check(
                "scale-freshness",
                "pass" if age <= max_age_seconds else "fail",
                f"scale evidence is {age:.0f}s old (limit {max_age_seconds:g}s)",
                age_seconds=age,
                max_age_seconds=max_age_seconds,
            )
        )
    return checks


def operational_evidence_checks(
    path: str | os.PathLike[str],
    *,
    evidence_type: str,
    max_age_seconds: float,
    min_duration_seconds: float | None = None,
    required_checks: Sequence[str] = (),
    now: dt.datetime | None = None,
) -> list[ReleaseCheck]:
    """Validate a current, runtime-bound operational exercise artifact."""
    target = Path(path).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            _check(
                f"{evidence_type}-evidence",
                "fail",
                f"{evidence_type} evidence is unreadable: {type(exc).__name__}: {exc}",
                path=str(target),
            )
        ]
    if not isinstance(payload, dict):
        return [_check(f"{evidence_type}-evidence", "fail", "operational evidence must be a JSON object")]

    expected_runtime = runtime_fingerprint()
    runtime_matches = payload.get("runtime_fingerprint") == expected_runtime
    checks = [
        _check(
            f"{evidence_type}-status",
            "pass" if payload.get("evidence_type") == evidence_type and payload.get("passed") is True else "fail",
            f"evidence_type={payload.get('evidence_type')!r}, passed={payload.get('passed')!r}",
            path=str(target),
        ),
        _check(
            f"{evidence_type}-runtime",
            "pass" if runtime_matches else "fail",
            "evidence matches current runtime" if runtime_matches else "evidence runtime differs from current code",
            observed=payload.get("runtime_fingerprint"),
        ),
    ]
    timestamp = _parse_timestamp(payload.get("generated_at") or payload.get("ended_at") or payload.get("timestamp"))
    current = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    if timestamp is None:
        checks.append(_check(f"{evidence_type}-freshness", "fail", "evidence timestamp is missing or invalid"))
    else:
        age = (current - timestamp).total_seconds()
        valid_age = -60 <= age <= max_age_seconds
        checks.append(
            _check(
                f"{evidence_type}-freshness",
                "pass" if valid_age else "fail",
                f"evidence age={age:.0f}s (allowed future skew=60s, max age={max_age_seconds:g}s)",
                age_seconds=age,
            )
        )
    if min_duration_seconds is not None:
        requested = payload.get("requested_duration_seconds")
        elapsed = payload.get("wall_elapsed_seconds", payload.get("elapsed_seconds"))
        duration_ok = (
            isinstance(requested, (int, float))
            and not isinstance(requested, bool)
            and requested >= min_duration_seconds
            and isinstance(elapsed, (int, float))
            and not isinstance(elapsed, bool)
            and elapsed >= min_duration_seconds * 0.95
        )
        checks.append(
            _check(
                f"{evidence_type}-duration",
                "pass" if duration_ok else "fail",
                f"requested={requested!r}s, elapsed={elapsed!r}s; required={min_duration_seconds:g}s",
            )
        )
    if evidence_type == "collector_soak":
        sampling = payload.get("sampling")
        store_integrity = payload.get("store_integrity")
        strict_soak = (
            payload.get("failed_samples") == 0
            and payload.get("uptime_ratio") == 1.0
            and isinstance(sampling, dict)
            and sampling.get("complete") is True
            and sampling.get("sample_gap_count") == 0
            and isinstance(store_integrity, dict)
            and store_integrity.get("verified") is True
            and store_integrity.get("prefix_unchanged") is True
        )
        checks.append(
            _check(
                "collector_soak-integrity",
                "pass" if strict_soak else "fail",
                "uptime, sampling, counters, and store prefix are strict-clean"
                if strict_soak
                else "strict soak integrity fields are incomplete or failed",
            )
        )
    if evidence_type == "recovery_drill":
        digest = payload.get("baseline_sha256")
        snapshot_events = payload.get("snapshot_events")
        identity_ok = (
            isinstance(digest, str)
            and len(digest) == 64
            and all(character in "0123456789abcdefABCDEF" for character in digest)
            and isinstance(snapshot_events, int)
            and not isinstance(snapshot_events, bool)
            and snapshot_events > 0
        )
        checks.append(
            _check(
                "recovery_drill-identity",
                "pass" if identity_ok else "fail",
                f"snapshot_events={snapshot_events!r}, baseline SHA-256 present={identity_ok}",
            )
        )
    if evidence_type == "billing_reconciliation":
        ledger_digest = payload.get("ledger_sha256")
        statement_digest = payload.get("external_statement_sha256")
        variance = payload.get("absolute_token_variance")
        tolerance = payload.get("token_variance_tolerance")
        scope_start = _parse_timestamp(payload.get("scope_start"))
        scope_end = _parse_timestamp(payload.get("scope_end"))

        def sha256_value(value: Any) -> bool:
            return (
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdefABCDEF" for character in value)
            )

        numeric_variance = (
            isinstance(variance, (int, float))
            and not isinstance(variance, bool)
            and isinstance(tolerance, (int, float))
            and not isinstance(tolerance, bool)
            and tolerance >= 0
        )
        billing_bound = (
            sha256_value(ledger_digest)
            and sha256_value(statement_digest)
            and scope_start is not None
            and scope_end is not None
            and scope_start < scope_end
            and numeric_variance
            and abs(float(variance)) <= float(tolerance)
        )
        checks.append(
            _check(
                "billing_reconciliation-binding",
                "pass" if billing_bound else "fail",
                f"scope and artifact hashes bound; variance={variance!r}, tolerance={tolerance!r}",
            )
        )
    if required_checks:
        observed_checks = payload.get("checks")
        check_items = observed_checks if isinstance(observed_checks, list) else []
        passed_names = {
            item.get("name")
            for item in check_items
            if isinstance(item, dict) and item.get("passed") is True
        }
        missing = sorted(set(required_checks) - passed_names)
        checks.append(
            _check(
                f"{evidence_type}-checks",
                "pass" if not missing else "fail",
                "all required exercise checks passed" if not missing else "missing passed checks: " + ", ".join(missing),
                missing=missing,
            )
        )
    return checks


def run_release_checks(
    *,
    provider_requirements: Sequence[str],
    dashboard_evidence: str,
    max_dashboard_age_seconds: float,
    min_pricing_coverage: float | None,
    min_latency_coverage: float | None,
    min_instrumented_latency_coverage: float | None = None,
    required_quality_status: str | None,
    scale_evidence: str | None = None,
    max_scale_age_seconds: float = 604800.0,
    min_scale_events: int = 50_000,
    provider_proof_dir: str | None = None,
    max_provider_proof_age_seconds: float = 2_592_000.0,
    provider_proof_capture_key_file: str | None = None,
    provider_proof_review_key_file: str | None = None,
    collector_soak_evidence: str | None = None,
    recovery_evidence: str | None = None,
    billing_evidence: str | None = None,
    max_operational_evidence_age_seconds: float = 2_592_000.0,
    min_collector_soak_seconds: float = 259_200.0,
    now: dt.datetime | None = None,
) -> list[ReleaseCheck]:
    manifest_checks, validations = provider_proof_manifest_checks(
        provider_proof_dir,
        max_age_seconds=max_provider_proof_age_seconds,
        capture_key_file=provider_proof_capture_key_file,
        review_key_file=provider_proof_review_key_file,
        now=now,
    )
    reviewed_capabilities = sorted(
        {
            capability
            for validation in validations
            if validation.valid
            for capability in validation.capabilities
        }
    )
    checks = [
        *manifest_checks,
        *provider_proof_checks(provider_requirements, reviewed_capabilities=reviewed_capabilities),
        *dashboard_evidence_checks(
            dashboard_evidence,
            max_age_seconds=max_dashboard_age_seconds,
            min_pricing_coverage=min_pricing_coverage,
            min_latency_coverage=min_latency_coverage,
            min_instrumented_latency_coverage=min_instrumented_latency_coverage,
            required_quality_status=required_quality_status,
            now=now,
        ),
    ]
    if scale_evidence is not None:
        checks.extend(
            scale_evidence_checks(
                scale_evidence,
                max_age_seconds=max_scale_age_seconds,
                min_event_count=min_scale_events,
                now=now,
            )
        )
    if collector_soak_evidence is not None:
        checks.extend(
            operational_evidence_checks(
                collector_soak_evidence,
                evidence_type="collector_soak",
                max_age_seconds=max_operational_evidence_age_seconds,
                min_duration_seconds=min_collector_soak_seconds,
                now=now,
            )
        )
    if recovery_evidence is not None:
        checks.extend(
            operational_evidence_checks(
                recovery_evidence,
                evidence_type="recovery_drill",
                max_age_seconds=max_operational_evidence_age_seconds,
                required_checks=(
                    "source_validation",
                    "backup_integrity",
                    "archive_first_retention",
                    "restore_integrity",
                    "duplicate_recovery",
                    "readability",
                ),
                now=now,
            )
        )
    if billing_evidence is not None:
        checks.extend(
            operational_evidence_checks(
                billing_evidence,
                evidence_type="billing_reconciliation",
                max_age_seconds=max_operational_evidence_age_seconds,
                required_checks=("external_statement_hashed", "scope_matched", "token_variance_within_tolerance"),
                now=now,
            )
        )
    return checks


def _render_text(checks: Sequence[ReleaseCheck]) -> str:
    lines = ["AI Token Tracker release readiness"]
    for item in checks:
        lines.append(f"[{item.status.upper()}] {item.name}: {item.detail}")
    failures = sum(item.failed for item in checks)
    warnings = sum(item.warned for item in checks)
    lines.append(f"summary: failures={failures} warnings={warnings} checks={len(checks)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail closed when delivery claims exceed available evidence")
    parser.add_argument("--require-proven", action="append", default=[], metavar="PROVIDER:SURFACE[:CAPABILITY]")
    parser.add_argument(
        "--dashboard-evidence",
        default=os.environ.get(
            "TRACKER_DASHBOARD_EVIDENCE",
            r"C:\ai-token-tracker-data\health\dashboard-refresh.json" if os.name == "nt" else "dashboard-refresh.json",
        ),
    )
    parser.add_argument("--max-dashboard-age-seconds", type=float, default=7200.0)
    parser.add_argument("--min-pricing-coverage", type=float)
    parser.add_argument("--min-latency-coverage", type=float)
    parser.add_argument("--min-instrumented-latency-coverage", type=float)
    parser.add_argument("--require-quality-status", choices=("clean", "warning", "blocked"))
    parser.add_argument("--scale-evidence")
    parser.add_argument("--max-scale-age-seconds", type=float, default=604800.0)
    parser.add_argument("--min-scale-events", type=int, default=50_000)
    parser.add_argument("--collector-soak-evidence")
    parser.add_argument("--recovery-evidence")
    parser.add_argument("--billing-evidence")
    parser.add_argument("--max-operational-evidence-age-seconds", type=float, default=2_592_000.0)
    parser.add_argument("--min-collector-soak-seconds", type=float, default=259_200.0)
    parser.add_argument(
        "--provider-proof-dir",
        default=os.environ.get("TRACKER_PROVIDER_PROOF_DIR"),
        help="directory containing explicitly reviewed, hash-bound live provider proofs",
    )
    parser.add_argument("--max-provider-proof-age-seconds", type=float, default=2_592_000.0)
    parser.add_argument(
        "--provider-proof-capture-key-file",
        default=os.environ.get("TRACKER_PROOF_CAPTURE_KEY_FILE"),
    )
    parser.add_argument(
        "--provider-proof-review-key-file",
        default=os.environ.get("TRACKER_PROOF_REVIEW_KEY_FILE"),
    )
    parser.add_argument("--strict-warnings", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    for name in ("min_pricing_coverage", "min_latency_coverage", "min_instrumented_latency_coverage"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    if (
        args.max_dashboard_age_seconds < 0
        or args.max_scale_age_seconds < 0
        or args.max_provider_proof_age_seconds < 0
        or args.max_operational_evidence_age_seconds < 0
        or args.min_collector_soak_seconds < 0
    ):
        parser.error("evidence age limits must be non-negative")
    if args.min_scale_events < 1:
        parser.error("--min-scale-events must be positive")

    checks = run_release_checks(
        provider_requirements=args.require_proven,
        dashboard_evidence=args.dashboard_evidence,
        max_dashboard_age_seconds=args.max_dashboard_age_seconds,
        min_pricing_coverage=args.min_pricing_coverage,
        min_latency_coverage=args.min_latency_coverage,
        min_instrumented_latency_coverage=args.min_instrumented_latency_coverage,
        required_quality_status=args.require_quality_status,
        scale_evidence=args.scale_evidence,
        max_scale_age_seconds=args.max_scale_age_seconds,
        min_scale_events=args.min_scale_events,
        provider_proof_dir=args.provider_proof_dir,
        max_provider_proof_age_seconds=args.max_provider_proof_age_seconds,
        provider_proof_capture_key_file=args.provider_proof_capture_key_file,
        provider_proof_review_key_file=args.provider_proof_review_key_file,
        collector_soak_evidence=args.collector_soak_evidence,
        recovery_evidence=args.recovery_evidence,
        billing_evidence=args.billing_evidence,
        max_operational_evidence_age_seconds=args.max_operational_evidence_age_seconds,
        min_collector_soak_seconds=args.min_collector_soak_seconds,
    )
    failures = sum(item.failed for item in checks)
    warnings = sum(item.warned for item in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "passed": failures == 0 and not (args.strict_warnings and warnings),
                    "failure_count": failures,
                    "warning_count": warnings,
                    "checks": [asdict(item) for item in checks],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_render_text(checks))
    if failures:
        return 1
    return 1 if args.strict_warnings and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
