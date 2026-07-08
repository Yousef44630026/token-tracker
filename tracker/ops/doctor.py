"""Operational readiness checks for local tracker deployments."""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tracker.analytics.coverage import build_coverage_exactness_from_events
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.observability.observation import Observation
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository

_DERIVED_EVENT_KEYS = {
    "event_contributing_tokens",
    "event_total_mismatch",
    "under_attributed_tokens",
    "over_attributed_tokens",
}
_DERIVED_QUANTITY_KEYS = {"included_in_total", "quantity_in_total", "export_warning"}


@dataclass(frozen=True)
class DoctorCheck:
    """One operational readiness check."""

    name: str
    status: str
    detail: str
    data: dict[str, Any] | None = None

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def warned(self) -> bool:
        return self.status == "warn"


def _check(name: str, status: str, detail: str, **data: Any) -> DoctorCheck:
    return DoctorCheck(name=name, status=status, detail=detail, data=data or None)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _python_check() -> DoctorCheck:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return _check("python", "pass", f"Python {version} is supported", executable=sys.executable)


def _import_check(module: str, *, required: bool) -> DoctorCheck:
    if importlib.util.find_spec(module) is not None:
        return _check(f"import:{module}", "pass", f"{module} is importable")
    status = "fail" if required else "warn"
    requirement = "required" if required else "optional"
    return _check(f"import:{module}", status, f"{module} is not importable ({requirement})")


def _storage_contract_check() -> DoctorCheck:
    event = TokenEvent(
        event_id="doctor-event",
        request_correlation_id="doctor-request",
        trace_id="doctor-trace",
        span_id="doctor-span",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                10,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=10,
        observation=Observation(authoritative=True, status="complete"),
    )
    payload = event.to_dict()
    event_leaks = sorted(_DERIVED_EVENT_KEYS & set(payload))
    quantity_leaks = sorted(_DERIVED_QUANTITY_KEYS & set(payload["quantities"][0]))
    if event_leaks or quantity_leaks:
        return _check(
            "storage-contract",
            "fail",
            "derived fields leaked into serialized source-of-truth payload",
            event_leaks=event_leaks,
            quantity_leaks=quantity_leaks,
        )
    if event.event_contributing_tokens != 10:
        return _check("storage-contract", "fail", "event contribution derivation disagrees with sample payload")
    return _check("storage-contract", "pass", "source-of-truth serialization excludes derived fields")


def _write_probe(path: str, *, partitioned: bool) -> DoctorCheck:
    target = Path(path).expanduser()
    probe_dir = target if partitioned else target.parent
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _check("store-writable", "fail", f"cannot create store directory: {exc}", path=str(probe_dir))
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=probe_dir, prefix="tracker-doctor-", delete=True) as handle:
            handle.write("ok\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        return _check("store-writable", "fail", f"cannot write a probe file: {exc}", path=str(probe_dir))
    return _check("store-writable", "pass", "store directory is writable", path=str(probe_dir))


def _store_events(path: str, *, partitioned: bool) -> Iterable[TokenEvent]:
    repository = PartitionedFileRepository(path) if partitioned else FileRepository(path)
    return repository.iter_events()


def _store_check(path: str, *, partitioned: bool) -> DoctorCheck:
    target = Path(path).expanduser()
    if not target.exists():
        return _check("store-read", "warn", "store does not exist yet; first run will create it", path=str(target))
    try:
        coverage = build_coverage_exactness_from_events(_store_events(str(target), partitioned=partitioned))
    except Exception as exc:  # noqa: BLE001 - doctor should report readiness, not crash
        return _check("store-read", "fail", f"store cannot be read cleanly: {type(exc).__name__}: {exc}", path=str(target))
    count = coverage["event_count"] + coverage["excluded_event_count"]
    status = "pass" if count else "warn"
    detail = (
        f"read {count} events; observed_total={coverage['observed_total_contributing_tokens']}"
        if count
        else "store is readable but contains no events"
    )
    return _check(
        "store-read",
        status,
        detail,
        path=str(target),
        partitioned=partitioned,
        event_count=count,
        observed_total_contributing_tokens=coverage["observed_total_contributing_tokens"],
        total_is_lower_bound=coverage["total_is_lower_bound"],
    )


def _network_posture_check(environment: dict[str, str]) -> DoctorCheck:
    host = environment.get("TRACKER_HOST", "127.0.0.1")
    auth_token = environment.get("TRACKER_AUTH_TOKEN")
    if _is_loopback(host):
        return _check("collector-network", "pass", "collector host is loopback by default", host=host)
    if auth_token:
        return _check("collector-network", "warn", "collector is non-loopback; ensure TLS/reverse-proxy protection", host=host)
    return _check("collector-network", "fail", "collector is non-loopback without TRACKER_AUTH_TOKEN", host=host)


def _azure_env_check(environment: dict[str, str]) -> DoctorCheck:
    required = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_VERSION")
    present = [key for key in required if environment.get(key)]
    missing = [key for key in required if not environment.get(key)]
    if not present:
        return _check("azure-openai-env", "info", "Azure OpenAI env vars are not set; OK unless running Azure live tests")
    if missing:
        return _check("azure-openai-env", "warn", "Azure OpenAI env is partial", present=present, missing=missing)
    return _check("azure-openai-env", "pass", "Azure OpenAI env vars are present", present=present)


def run_checks(
    *,
    store: str,
    partitioned_store: bool = False,
    skip_store: bool = False,
    environment: dict[str, str] | None = None,
) -> list[DoctorCheck]:
    """Run operational readiness checks."""
    env = dict(os.environ if environment is None else environment)
    checks = [
        _python_check(),
        _import_check("tracker", required=True),
        _import_check("api.main", required=True),
        _import_check("openpyxl", required=True),
        _import_check("ruff", required=False),
        _storage_contract_check(),
        _network_posture_check(env),
        _azure_env_check(env),
    ]
    if not skip_store:
        checks.append(_write_probe(store, partitioned=partitioned_store))
        checks.append(_store_check(store, partitioned=partitioned_store))
    return checks


def _render_text(checks: Sequence[DoctorCheck]) -> str:
    lines = ["AI Token Tracker operational doctor"]
    for item in checks:
        lines.append(f"[{item.status.upper()}] {item.name}: {item.detail}")
    failures = sum(1 for item in checks if item.failed)
    warnings = sum(1 for item in checks if item.warned)
    lines.append(f"summary: failures={failures} warnings={warnings} checks={len(checks)}")
    return "\n".join(lines)


def _render_json(checks: Sequence[DoctorCheck]) -> str:
    failures = sum(1 for item in checks if item.failed)
    warnings = sum(1 for item in checks if item.warned)
    payload = {
        "passed": failures == 0,
        "failure_count": failures,
        "warning_count": warnings,
        "checks": [asdict(item) for item in checks],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check AI Token Tracker operational readiness")
    parser.add_argument("--store", default=os.environ.get("TRACKER_STORE", "collector_events.jsonl"))
    parser.add_argument("--partitioned-store", action="store_true", help="treat --store as a date/trace partitioned repository root")
    parser.add_argument("--skip-store", action="store_true", help="skip store write/read checks")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict-warnings", action="store_true", help="return non-zero when warnings are present")
    args = parser.parse_args(argv)

    checks = run_checks(store=args.store, partitioned_store=args.partitioned_store, skip_store=args.skip_store)
    failures = sum(1 for item in checks if item.failed)
    warnings = sum(1 for item in checks if item.warned)
    print(_render_json(checks) if args.json else _render_text(checks))
    if failures:
        return 1
    return 1 if args.strict_warnings and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
