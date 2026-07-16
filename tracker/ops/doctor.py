"""Operational readiness checks for local tracker deployments."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import ipaddress
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tracker.analytics.coverage import build_coverage_exactness_from_events
from tracker.estimation.local_tokenizer import tokenizer_status
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
    "superseded",
    "superseded_by",
}
_DERIVED_QUANTITY_KEYS = {"additivity", "included_in_total", "quantity_in_total", "export_warning"}
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer_token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("azure_key_shaped", re.compile(r"\b[A-Za-z0-9]{80,100}\b")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)
_SECRET_SCAN_ROOTS = ("tracker", "api", "scripts", "docs", "examples", "tests", ".github")
_SECRET_SCAN_FILES = ("README.md", "pyproject.toml", ".env.example")
_LOCAL_SECRET_FILES = (".env",)
_MAX_SECRET_SCAN_BYTES = 1_000_000
_DEFAULT_MAX_HEALTH_AGE_SECONDS = 300.0
_DEFAULT_MAX_CLAUDE_IMPORT_AGE_SECONDS = 7_200.0
_DEFAULT_MAX_DASHBOARD_AGE_SECONDS = 7_200.0
_HEALTH_TAIL_BYTES = 65_536


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


def _python_check(version_info: Sequence[int] | None = None) -> DoctorCheck:
    selected = version_info or sys.version_info
    major, minor = int(selected[0]), int(selected[1])
    micro = int(selected[2]) if len(selected) > 2 else 0
    version = f"{major}.{minor}.{micro}"
    if (major, minor) < (3, 11):
        return _check(
            "python",
            "fail",
            f"Python {version} is unsupported; Python 3.11+ is required",
            executable=sys.executable,
            required=">=3.11",
        )
    return _check("python", "pass", f"Python {version} is supported", executable=sys.executable, required=">=3.11")


def _durability_check(environment: Mapping[str, str]) -> DoctorCheck:
    settings = ("TRACKER_DURABLE", "TRACKER_PROXY_DURABLE")
    disabled: list[str] = []
    invalid: dict[str, str] = {}
    for name in settings:
        raw = environment.get(name)
        if raw is None or not raw.strip():
            continue  # both operational CLIs default to durable writes
        normalized = raw.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            disabled.append(name)
        elif normalized not in {"1", "true", "yes", "on"}:
            invalid[name] = raw
    if invalid:
        return _check(
            "durable-persistence",
            "fail",
            "invalid durability environment value",
            invalid_keys=sorted(invalid),
        )
    if disabled:
        return _check(
            "durable-persistence",
            "warn",
            "acknowledged events may be lost on a crash because durable writes are disabled",
            disabled=disabled,
        )
    return _check("durable-persistence", "pass", "collector and proxy durable writes are enabled by default")


def _tokenizer_check() -> DoctorCheck:
    status = tokenizer_status()
    backend = str(status["backend"])
    if status["tokenizer_available"]:
        return _check(
            "tokenizer-backend",
            "pass",
            f"local estimates use {backend}",
            **status,
        )
    return _check(
        "tokenizer-backend",
        "warn",
        "tiktoken is unavailable; interrupted-stream estimates use the coarse char4 fallback",
        **status,
    )


def _storage_substrate_check(store: str) -> DoctorCheck:
    path = os.path.abspath(os.path.expanduser(store))
    normalized = path.replace("\\", "/").lower()
    markers = {
        "onedrive": "OneDrive",
        "dropbox": "Dropbox",
        "google drive": "Google Drive",
    }
    matched = next((label for marker, label in markers.items() if marker in normalized), None)
    if matched:
        return _check(
            "storage-substrate",
            "warn",
            f"event store is inside {matched}; sync engines can stall locks or fork append-only ledgers",
            store=path,
            recommendation="move TRACKER_STORE to a non-synced local volume and export copies for sharing",
        )
    return _check("storage-substrate", "pass", "event store path is not inside a recognized sync folder", store=path)


def _last_health_record(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - _HEALTH_TAIL_BYTES))
        tail = handle.read()
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    if not lines:
        raise ValueError("health evidence is empty")
    payload = json.loads(lines[-1].decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("latest health evidence must be a JSON object")
    return payload


def _health_evidence_check(
    health_log: str,
    *,
    max_age_seconds: float = _DEFAULT_MAX_HEALTH_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> DoctorCheck:
    path = Path(health_log).expanduser().resolve()
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        return _check(
            "collector-health-evidence",
            "fail",
            "maximum health evidence age must be a positive number",
            path=str(path),
        )
    if not path.exists():
        return _check(
            "collector-health-evidence",
            "warn",
            "collector health evidence does not exist yet",
            path=str(path),
            max_age_seconds=max_age_seconds,
        )
    try:
        sample = _last_health_record(path)
        raw_timestamp = sample.get("timestamp")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise ValueError("latest health evidence has no timestamp")
        observed_at = dt.datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("latest health evidence timestamp has no timezone")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _check(
            "collector-health-evidence",
            "fail",
            f"latest collector health evidence is unreadable: {type(exc).__name__}: {exc}",
            path=str(path),
        )

    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    age_seconds = (current.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)).total_seconds()
    common = {
        "path": str(path),
        "timestamp": raw_timestamp,
        "age_seconds": round(age_seconds, 3),
        "max_age_seconds": max_age_seconds,
        "healthy": sample.get("healthy"),
        "collector_status": sample.get("status"),
    }
    if age_seconds < -60:
        return _check(
            "collector-health-evidence",
            "fail",
            "latest collector health evidence is timestamped in the future",
            **common,
        )
    if age_seconds > max_age_seconds:
        return _check(
            "collector-health-evidence",
            "fail",
            f"latest collector health evidence is stale ({age_seconds:.0f}s old)",
            **common,
        )
    if sample.get("healthy") is not True:
        return _check(
            "collector-health-evidence",
            "fail",
            "latest collector health probe reports the collector unavailable",
            **common,
        )
    return _check(
        "collector-health-evidence",
        "pass",
        f"latest collector health evidence is fresh ({max(0.0, age_seconds):.0f}s old)",
        **common,
    )


def _claude_import_evidence_check(
    import_log: str,
    *,
    max_age_seconds: float = _DEFAULT_MAX_CLAUDE_IMPORT_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> DoctorCheck:
    """Fail when the scheduled Claude import is stale, unreadable, or unhealthy."""
    path = Path(import_log).expanduser().resolve()
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        return _check(
            "claude-import-evidence",
            "fail",
            "maximum Claude import evidence age must be a positive number",
            path=str(path),
        )
    if not path.exists():
        return _check(
            "claude-import-evidence",
            "warn",
            "Claude import evidence does not exist yet",
            path=str(path),
            max_age_seconds=max_age_seconds,
        )
    try:
        sample = _last_health_record(path)
        raw_timestamp = sample.get("timestamp")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise ValueError("latest Claude import evidence has no timestamp")
        observed_at = dt.datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("latest Claude import evidence timestamp has no timezone")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _check(
            "claude-import-evidence",
            "fail",
            f"latest Claude import evidence is unreadable: {type(exc).__name__}: {exc}",
            path=str(path),
        )

    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    age_seconds = (current.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)).total_seconds()
    status = sample.get("status")
    report = sample.get("import_report")
    format_drift = isinstance(report, dict) and report.get("format_drift_suspected") is True
    common = {
        "path": str(path),
        "timestamp": raw_timestamp,
        "age_seconds": round(age_seconds, 3),
        "max_age_seconds": max_age_seconds,
        "import_status": status,
        "format_drift_suspected": format_drift,
    }
    if age_seconds < -60:
        return _check(
            "claude-import-evidence",
            "fail",
            "latest Claude import evidence is timestamped in the future",
            **common,
        )
    if age_seconds > max_age_seconds:
        return _check(
            "claude-import-evidence",
            "fail",
            f"latest Claude import evidence is stale ({age_seconds:.0f}s old)",
            **common,
        )
    if status != "ok" or format_drift:
        return _check(
            "claude-import-evidence",
            "fail",
            f"latest Claude import run is unhealthy ({status or 'missing_status'})",
            **common,
        )
    return _check(
        "claude-import-evidence",
        "pass",
        f"latest Claude import evidence is healthy and fresh ({max(0.0, age_seconds):.0f}s old)",
        **common,
    )


def _dashboard_evidence_check(
    evidence_file: str,
    *,
    max_age_seconds: float = _DEFAULT_MAX_DASHBOARD_AGE_SECONDS,
    now: dt.datetime | None = None,
) -> DoctorCheck:
    """Fail when the scheduled dashboard refresh is stale, incomplete, or unhealthy."""
    path = Path(evidence_file).expanduser().resolve()
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(max_age_seconds)
        or max_age_seconds <= 0
    ):
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            "maximum dashboard evidence age must be a positive number",
            path=str(path),
        )
    if not path.exists():
        return _check(
            "dashboard-refresh-evidence",
            "warn",
            "dashboard refresh evidence does not exist yet",
            path=str(path),
            max_age_seconds=max_age_seconds,
        )
    try:
        sample = _last_health_record(path)
        raw_timestamp = sample.get("timestamp")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise ValueError("dashboard evidence has no timestamp")
        observed_at = dt.datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            raise ValueError("dashboard evidence timestamp has no timezone")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            f"latest dashboard refresh evidence is unreadable: {type(exc).__name__}: {exc}",
            path=str(path),
        )

    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    age_seconds = (current.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)).total_seconds()
    status = sample.get("status")
    report = sample.get("report")
    output_file = sample.get("output_file")
    skipped_lines = report.get("skipped_lines") if isinstance(report, dict) else None
    duplicate_event_ids = report.get("duplicate_event_ids") if isinstance(report, dict) else None
    valid_events = report.get("valid_events") if isinstance(report, dict) else None
    common = {
        "path": str(path),
        "timestamp": raw_timestamp,
        "age_seconds": round(age_seconds, 3),
        "max_age_seconds": max_age_seconds,
        "refresh_status": status,
        "output_file": output_file,
        "valid_events": valid_events,
        "skipped_lines": skipped_lines,
        "duplicate_event_ids": duplicate_event_ids,
    }
    if age_seconds < -60:
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            "latest dashboard refresh evidence is timestamped in the future",
            **common,
        )
    if age_seconds > max_age_seconds:
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            f"latest dashboard refresh evidence is stale ({age_seconds:.0f}s old)",
            **common,
        )
    if status != "ok":
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            f"latest dashboard refresh is unhealthy ({status or 'missing_status'})",
            **common,
        )
    if not isinstance(output_file, str) or not Path(output_file).expanduser().is_file():
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            "latest dashboard refresh output is missing",
            **common,
        )
    if isinstance(valid_events, bool) or not isinstance(valid_events, int) or valid_events < 0:
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            "latest dashboard refresh report has an invalid event count",
            **common,
        )
    if skipped_lines != 0 or duplicate_event_ids != 0:
        return _check(
            "dashboard-refresh-evidence",
            "fail",
            "latest dashboard refresh omitted or duplicated source rows",
            **common,
        )
    return _check(
        "dashboard-refresh-evidence",
        "pass",
        f"latest dashboard refresh is healthy and fresh ({max(0.0, age_seconds):.0f}s old)",
        **common,
    )


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
    if payload.get("schema_version") != 9 or not isinstance(payload.get("observation", {}).get("authoritative"), bool):
        return _check(
            "storage-contract",
            "fail",
            "serialized event lacks schema v9 or explicit boolean authority",
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
    repository = (
        PartitionedFileRepository(path, skip_invalid_records=False) if partitioned else FileRepository(path, skip_invalid_records=False)
    )
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
    detail = (
        f"read {count} events; observed_total={coverage['observed_total_contributing_tokens']}"
        if count
        else "store is initialized, readable, and contains no events yet"
    )
    return _check(
        "store-read",
        "pass",
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


def _secret_scan_candidates(root: Path) -> Iterable[Path]:
    for name in _SECRET_SCAN_FILES + _LOCAL_SECRET_FILES:
        candidate = root / name
        if candidate.is_file():
            yield candidate
    for directory_name in _SECRET_SCAN_ROOTS:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".cmd", ".json", ".md", ".ps1", ".py", ".toml", ".yaml", ".yml"}:
                yield path


def _secret_scan_check(root: str) -> DoctorCheck:
    base = Path(root).resolve()
    findings: list[dict[str, Any]] = []
    local_secret_findings = 0
    for path in _secret_scan_candidates(base):
        try:
            if path.stat().st_size > _MAX_SECRET_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = str(path.relative_to(base))
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    item = {"path": relative, "line": line_number, "kind": name}
                    if path.name in _LOCAL_SECRET_FILES:
                        local_secret_findings += 1
                        item["local_secret_file"] = True
                    findings.append(item)
    if not findings:
        return _check("secret-scan", "pass", "no credential-shaped values found in checked project files", root=str(base))
    if len(findings) == local_secret_findings:
        return _check(
            "secret-scan",
            "warn",
            "credential-shaped values found only in local ignored secret files",
            root=str(base),
            finding_count=len(findings),
            findings=findings[:10],
        )
    return _check(
        "secret-scan",
        "fail",
        "credential-shaped values found in project files; rotate exposed credentials before sharing/committing",
        root=str(base),
        finding_count=len(findings),
        findings=findings[:10],
    )


def _azure_env_check(environment: dict[str, str]) -> DoctorCheck:
    profiles = {
        "foundry-responses": (
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_RESPONSES_ENDPOINT",
            "AZURE_OPENAI_RESPONSES_DEPLOYMENT",
        ),
        "azure-chat": (
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_DEPLOYMENT",
        ),
        "azure-embeddings": (
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",
        ),
    }
    profile_specific_keys = {
        "foundry-responses": ("AZURE_OPENAI_RESPONSES_ENDPOINT", "AZURE_OPENAI_RESPONSES_DEPLOYMENT"),
        "azure-chat": ("AZURE_OPENAI_DEPLOYMENT",),
        "azure-embeddings": ("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT",),
    }
    azure_keys = sorted(key for key in environment if key.startswith("AZURE_OPENAI_") and environment.get(key))
    configured: list[str] = []
    present_by_profile: dict[str, list[str]] = {}
    partial: dict[str, list[str]] = {}
    for profile, required in profiles.items():
        present = [key for key in required if environment.get(key)]
        present_by_profile[profile] = present
        if len(present) == len(required):
            configured.append(profile)
    for profile, required in profiles.items():
        if profile in configured:
            continue
        present = present_by_profile[profile]
        surface_hint_present = any(environment.get(key) for key in profile_specific_keys[profile])
        if surface_hint_present or (present and not configured):
            partial[profile] = [key for key in required if not environment.get(key)]
    if not azure_keys:
        return _check("azure-openai-env", "info", "Azure/Foundry env vars are not set; OK unless running live tests")
    if configured:
        detail = "Azure/Foundry env configured for: " + ", ".join(configured)
        if partial:
            return _check("azure-openai-env", "warn", detail + "; some optional surfaces are partial", profiles=configured, partial=partial)
        return _check("azure-openai-env", "pass", detail, profiles=configured)
    return _check(
        "azure-openai-env",
        "warn",
        "Azure/Foundry env is partial; no runnable live surface detected",
        present=azure_keys,
        partial=partial,
    )


def run_checks(
    *,
    store: str,
    partitioned_store: bool = False,
    skip_store: bool = False,
    environment: dict[str, str] | None = None,
    secret_scan_root: str | None = None,
    health_log: str | None = None,
    max_health_age_seconds: float = _DEFAULT_MAX_HEALTH_AGE_SECONDS,
    claude_import_log: str | None = None,
    max_claude_import_age_seconds: float = _DEFAULT_MAX_CLAUDE_IMPORT_AGE_SECONDS,
    dashboard_evidence_file: str | None = None,
    max_dashboard_age_seconds: float = _DEFAULT_MAX_DASHBOARD_AGE_SECONDS,
) -> list[DoctorCheck]:
    """Run operational readiness checks."""
    env = dict(os.environ if environment is None else environment)
    selected_health_log = health_log or env.get("TRACKER_HEALTH_LOG") or os.path.join(
        os.path.dirname(os.path.abspath(os.path.expanduser(store))),
        "health",
        "collector-health.jsonl",
    )
    selected_claude_import_log = claude_import_log or env.get("TRACKER_CLAUDE_IMPORT_LOG") or os.path.join(
        os.path.dirname(os.path.abspath(os.path.expanduser(store))),
        "health",
        "claude-import.log",
    )
    selected_dashboard_evidence = dashboard_evidence_file or env.get("TRACKER_DASHBOARD_EVIDENCE") or os.path.join(
        os.path.dirname(os.path.abspath(os.path.expanduser(store))),
        "health",
        "dashboard-refresh.json",
    )
    checks = [
        _python_check(),
        _import_check("tracker", required=True),
        _import_check("api.main", required=True),
        _import_check("openpyxl", required=True),
        _import_check("ruff", required=False),
        _storage_contract_check(),
        _network_posture_check(env),
        _durability_check(env),
        _tokenizer_check(),
        _storage_substrate_check(store),
        _health_evidence_check(selected_health_log, max_age_seconds=max_health_age_seconds),
        _claude_import_evidence_check(
            selected_claude_import_log,
            max_age_seconds=max_claude_import_age_seconds,
        ),
        _dashboard_evidence_check(
            selected_dashboard_evidence,
            max_age_seconds=max_dashboard_age_seconds,
        ),
        _secret_scan_check(secret_scan_root or os.getcwd()),
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
    default_store = os.environ.get("TRACKER_STORE") or (
        r"C:\ai-token-tracker-data\collector_events.jsonl" if os.name == "nt" else "collector_events.jsonl"
    )
    parser.add_argument("--store", default=default_store)
    parser.add_argument("--partitioned-store", action="store_true", help="treat --store as a date/trace partitioned repository root")
    parser.add_argument("--skip-store", action="store_true", help="skip store write/read checks")
    parser.add_argument(
        "--secret-scan-root",
        default=os.getcwd(),
        help="project directory to scan for credential-shaped values (default: current directory)",
    )
    parser.add_argument(
        "--health-log",
        default=os.environ.get("TRACKER_HEALTH_LOG"),
        help="collector health JSONL (default: beside the selected store)",
    )
    parser.add_argument(
        "--max-health-age-seconds",
        type=float,
        default=os.environ.get("TRACKER_HEALTH_STALE_SECONDS", str(_DEFAULT_MAX_HEALTH_AGE_SECONDS)),
        help="fail when the latest collector health evidence is older than this age",
    )
    parser.add_argument(
        "--claude-import-log",
        default=os.environ.get("TRACKER_CLAUDE_IMPORT_LOG"),
        help="scheduled Claude import JSONL log (default: beside the selected store)",
    )
    parser.add_argument(
        "--max-claude-import-age-seconds",
        type=float,
        default=os.environ.get(
            "TRACKER_CLAUDE_IMPORT_STALE_SECONDS",
            str(_DEFAULT_MAX_CLAUDE_IMPORT_AGE_SECONDS),
        ),
        help="fail when the latest Claude import evidence is older than this age",
    )
    parser.add_argument(
        "--dashboard-evidence-file",
        default=os.environ.get("TRACKER_DASHBOARD_EVIDENCE"),
        help="scheduled dashboard refresh JSON evidence (default: beside the selected store)",
    )
    parser.add_argument(
        "--max-dashboard-age-seconds",
        type=float,
        default=os.environ.get(
            "TRACKER_DASHBOARD_STALE_SECONDS",
            str(_DEFAULT_MAX_DASHBOARD_AGE_SECONDS),
        ),
        help="fail when the latest dashboard refresh evidence is older than this age",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict-warnings", action="store_true", help="return non-zero when warnings are present")
    args = parser.parse_args(argv)

    checks = run_checks(
        store=args.store,
        partitioned_store=args.partitioned_store,
        skip_store=args.skip_store,
        secret_scan_root=args.secret_scan_root,
        health_log=args.health_log,
        max_health_age_seconds=args.max_health_age_seconds,
        claude_import_log=args.claude_import_log,
        max_claude_import_age_seconds=args.max_claude_import_age_seconds,
        dashboard_evidence_file=args.dashboard_evidence_file,
        max_dashboard_age_seconds=args.max_dashboard_age_seconds,
    )
    failures = sum(1 for item in checks if item.failed)
    warnings = sum(1 for item in checks if item.warned)
    print(_render_json(checks) if args.json else _render_text(checks))
    if failures:
        return 1
    return 1 if args.strict_warnings and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
