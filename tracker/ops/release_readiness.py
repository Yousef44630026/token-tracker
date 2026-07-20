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


def provider_proof_checks(requirements: Sequence[str]) -> list[ReleaseCheck]:
    records = realistic_fixture_records()
    surfaces = build_provider_validation_matrix(records)
    capabilities = build_capability_certification_matrix(records, PROVIDER_CAPABILITY_POLICIES)
    try:
        failures = certification_requirement_failures(surfaces, capabilities, requirements)
    except ValueError as exc:
        return [_check("provider-proof", "fail", str(exc))]
    failed_by_requirement = {failure.split("=", 1)[0]: failure.split("=", 1)[1] for failure in failures}
    return [
        _check(
            f"provider-proof:{requirement}",
            "fail" if requirement in failed_by_requirement else "pass",
            (
                f"REAL proof required; current certification={failed_by_requirement[requirement]}"
                if requirement in failed_by_requirement
                else "REAL fixture proves the required surface or capability"
            ),
            requirement=requirement,
            certification=failed_by_requirement.get(requirement, "proven"),
        )
        for requirement in requirements
    ]


def dashboard_evidence_checks(
    path: str | os.PathLike[str],
    *,
    max_age_seconds: float,
    min_pricing_coverage: float | None = None,
    min_latency_coverage: float | None = None,
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


def run_release_checks(
    *,
    provider_requirements: Sequence[str],
    dashboard_evidence: str,
    max_dashboard_age_seconds: float,
    min_pricing_coverage: float | None,
    min_latency_coverage: float | None,
    required_quality_status: str | None,
    now: dt.datetime | None = None,
) -> list[ReleaseCheck]:
    return [
        *provider_proof_checks(provider_requirements),
        *dashboard_evidence_checks(
            dashboard_evidence,
            max_age_seconds=max_dashboard_age_seconds,
            min_pricing_coverage=min_pricing_coverage,
            min_latency_coverage=min_latency_coverage,
            required_quality_status=required_quality_status,
            now=now,
        ),
    ]


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
    parser.add_argument("--require-quality-status", choices=("clean", "warning", "blocked"))
    parser.add_argument("--strict-warnings", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    for name in ("min_pricing_coverage", "min_latency_coverage"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    if args.max_dashboard_age_seconds < 0:
        parser.error("--max-dashboard-age-seconds must be non-negative")

    checks = run_release_checks(
        provider_requirements=args.require_proven,
        dashboard_evidence=args.dashboard_evidence,
        max_dashboard_age_seconds=args.max_dashboard_age_seconds,
        min_pricing_coverage=args.min_pricing_coverage,
        min_latency_coverage=args.min_latency_coverage,
        required_quality_status=args.require_quality_status,
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
