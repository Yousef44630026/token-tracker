"""Human-reviewed, hash-bound evidence for live provider capabilities."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracker.models.enums import DataQualityFlag, PrecisionLevel, TokenType, Trust, UsageSource
from tracker.models.token_event import TokenEvent
from tracker.ops.runtime_fingerprint import runtime_fingerprint

PROOF_SCHEMA_VERSION = 1
CAPTURE_ATTESTATION_SCHEMA_VERSION = 1
_CACHE_TYPES = frozenset({TokenType.CACHED_INPUT, TokenType.CACHE_CREATION_INPUT})
_BLOCKING_FLAGS = frozenset(
    {
        DataQualityFlag.AUTHORITY_MISSING.value,
        DataQualityFlag.UNVERIFIED_ADDITIVITY.value,
        DataQualityFlag.UNKNOWN_QUANTITY_PRESENT.value,
        DataQualityFlag.PROVIDER_TOTAL_MISMATCH.value,
        DataQualityFlag.PROVIDER_TOTAL_OVER_ATTRIBUTION.value,
        DataQualityFlag.RAW_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value,
        DataQualityFlag.PROVIDER_RESPONSE_UNPARSEABLE.value,
        DataQualityFlag.NORMALIZATION_ERROR.value,
        DataQualityFlag.PROVIDER_USAGE_UNVERIFIED.value,
    }
)


@dataclass(frozen=True)
class ProofValidation:
    path: str
    valid: bool
    detail: str
    capabilities: tuple[str, ...] = ()
    proof_id: str | None = None


def _timestamp() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _read_key(path: str | Path) -> bytes:
    target = Path(path).expanduser().resolve()
    try:
        key = target.read_bytes()
    except OSError as exc:
        raise ValueError(f"unreadable proof key: {target}: {type(exc).__name__}: {exc}") from exc
    if len(key) < 32:
        raise ValueError(f"proof key must contain at least 32 bytes: {target}")
    return key


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _sign(payload: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical_json(payload), hashlib.sha256).hexdigest()


def _verify_signature(payload: Mapping[str, Any], signature: Any, key: bytes) -> bool:
    return isinstance(signature, str) and hmac.compare_digest(_sign(payload, key), signature)


def initialize_proof_keys(directory: str | Path) -> dict[str, str]:
    target = Path(directory).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    paths = {"capture_key": target / "capture.key", "review_key": target / "review.key"}
    if any(path.exists() for path in paths.values()):
        raise ValueError(f"refusing to overwrite an existing proof key in {target}")
    for path in paths.values():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, secrets.token_bytes(32))
        finally:
            os.close(descriptor)
    return {name: str(path) for name, path in paths.items()}


def source_identity(root: str | Path | None = None) -> dict[str, Any]:
    project_root = Path(root).expanduser().resolve() if root is not None else Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cannot inspect Git source identity: {type(exc).__name__}: {exc}") from exc
    sha = commit.stdout.strip() if commit.returncode == 0 else ""
    if len(sha) != 40 or status.returncode != 0:
        raise ValueError("provider proof requires a Git checkout with a resolvable commit")
    return {"git_commit": sha, "git_worktree_clean": not bool(status.stdout.strip())}


def _path_record(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if not target.exists():
        raise ValueError(f"proof artifact does not exist: {target}")
    if target.is_file():
        return {
            "path": str(target),
            "kind": "file",
            "sha256": _sha256_file(target),
            "size_bytes": target.stat().st_size,
        }
    digest = hashlib.sha256()
    total_size = 0
    file_count = 0
    for child in sorted(path for path in target.rglob("*") if path.is_file()):
        relative = child.relative_to(target).as_posix().encode("utf-8")
        child_hash = bytes.fromhex(_sha256_file(child))
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(child_hash)
        total_size += child.stat().st_size
        file_count += 1
    return {
        "path": str(target),
        "kind": "directory",
        "sha256": digest.hexdigest(),
        "size_bytes": total_size,
        "file_count": file_count,
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON evidence: {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON evidence must be an object: {path}")
    return payload


def _summary_artifact_records(summary_path: Path, summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {"source_summary": _path_record(summary_path)}
    artifacts = summary.get("artifacts")
    if isinstance(artifacts, Mapping):
        for name, value in sorted(artifacts.items()):
            if name in {"summary", "capture_attestation"} or not isinstance(value, str) or not value:
                continue
            records[str(name)] = _path_record(Path(value))
    results = summary.get("results")
    if isinstance(results, list):
        for index, result in enumerate(results):
            value = result.get("artifact") if isinstance(result, Mapping) else None
            if isinstance(value, str) and value:
                records[f"result_{index:03d}"] = _path_record(Path(value))
    return records


def write_capture_attestation(
    summary_path: str | Path,
    key_path: str | Path,
    *,
    harness: str,
    identity: Mapping[str, Any] | None = None,
) -> str:
    summary_target = Path(summary_path).expanduser().resolve()
    summary = _load_json_object(summary_target)
    key = _read_key(key_path)
    observed_identity = dict(identity or source_identity())
    if not observed_identity.get("git_commit"):
        raise ValueError("capture attestation requires a Git commit")
    payload = {
        "schema_version": CAPTURE_ATTESTATION_SCHEMA_VERSION,
        "harness": harness,
        "captured_at": summary.get("generated_at"),
        "runtime_fingerprint": summary.get("runtime_fingerprint"),
        "git_commit": observed_identity["git_commit"],
        "git_worktree_clean": observed_identity.get("git_worktree_clean") is True,
        "capture_key_id": _key_id(key),
        "artifacts": _summary_artifact_records(summary_target, summary),
    }
    attestation = {**payload, "hmac_sha256": _sign(payload, key)}
    output = summary_target.with_name("capture_attestation.json")
    output.write_text(json.dumps(attestation, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return str(output)


def _validate_artifact_records(records: Any) -> None:
    if not isinstance(records, Mapping) or "source_summary" not in records:
        raise ValueError("provider proof artifact hashes are missing")
    for name, record in records.items():
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise ValueError(f"invalid artifact record: {name}")
        observed = _path_record(Path(record["path"]))
        if observed["sha256"] != record.get("sha256"):
            raise ValueError(f"artifact hash mismatch: {name}")


def _validate_capture_attestation(summary_path: Path, key: bytes) -> dict[str, Any]:
    attestation_path = summary_path.with_name("capture_attestation.json")
    attestation = _load_json_object(attestation_path)
    signature = attestation.pop("hmac_sha256", None)
    if attestation.get("schema_version") != CAPTURE_ATTESTATION_SCHEMA_VERSION:
        raise ValueError("unsupported capture attestation schema_version")
    if attestation.get("capture_key_id") != _key_id(key) or not _verify_signature(attestation, signature, key):
        raise ValueError("capture attestation signature is invalid")
    if attestation.get("git_worktree_clean") is not True:
        raise ValueError("provider proof capture was produced from a dirty Git worktree")
    current_identity = source_identity()
    if attestation.get("git_commit") != current_identity["git_commit"]:
        raise ValueError("provider proof Git commit differs from the current checkout")
    if attestation.get("runtime_fingerprint") != runtime_fingerprint():
        raise ValueError("capture attestation runtime fingerprint differs from current code")
    _validate_artifact_records(attestation.get("artifacts"))
    source_summary = Path(attestation["artifacts"]["source_summary"]["path"])
    if source_summary.resolve() != summary_path.resolve():
        raise ValueError("capture attestation points to another summary")
    return attestation


def _age_seconds(value: Any, *, now: dt.datetime, label: str, future_skew_seconds: float = 60.0) -> float:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        raise ValueError(f"{label} is missing or invalid")
    delta = (now.astimezone(dt.UTC) - timestamp).total_seconds()
    if delta < -future_skew_seconds:
        raise ValueError(f"{label} is in the future")
    return max(delta, 0.0)


def _strict_events(path: Path) -> list[TokenEvent]:
    events: list[TokenEvent] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"unreadable proof events: {path}: {type(exc).__name__}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("event row must be an object")
            events.append(TokenEvent.from_dict(payload, require_explicit_authority=True))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid proof event at line {line_number}: {type(exc).__name__}: {exc}") from exc
    if not events:
        raise ValueError("proof event artifact contains no events")
    return events


def _event_capabilities(event: TokenEvent) -> set[str]:
    if not event.is_authoritative or not event.provider or not event.api_surface:
        return set()
    if set(event.data_quality_flags) & _BLOCKING_FLAGS:
        return set()
    if event.provider_total_tokens is None or event.event_total_mismatch != 0 or event.over_attributed_tokens:
        return set()
    provider_identity = event.observation.get("provider_request_id") or event.observation.get("provider_response_id")
    http_status = event.observation.get("http_status")
    bounded_provider_metadata = (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 200 <= http_status < 300
        and bool(event.observation.get("cloud_provider"))
        and bool(event.observation.get("deployment"))
    )
    if not provider_identity and not bounded_provider_metadata:
        return set()
    contributing = [quantity for quantity in event.quantities if quantity.quantity_in_total > 0]
    if not contributing or event.event_contributing_tokens <= 0:
        return set()
    if any(
        quantity.quantity is None
        or quantity.precision_level != PrecisionLevel.EXACT
        or quantity.trust != Trust.VERIFIED
        for quantity in event.quantities
    ):
        return set()
    prefix = f"{event.provider}:{event.api_surface}"
    capabilities = {f"{prefix}:usage"}
    if all(quantity.usage_source == UsageSource.PROVIDER_STREAM_FINAL for quantity in contributing):
        capabilities.add(f"{prefix}:stream")
    if any(
        quantity.token_type in _CACHE_TYPES and quantity.quantity is not None and quantity.quantity > 0
        for quantity in event.quantities
    ):
        capabilities.add(f"{prefix}:cache")
    return capabilities


def observed_capabilities(events: Sequence[TokenEvent]) -> tuple[str, ...]:
    capabilities: set[str] = set()
    for event in events:
        capabilities.update(_event_capabilities(event))
    return tuple(sorted(capabilities))


def _validated_summary(
    summary_path: Path,
    *,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], Path, list[TokenEvent]]:
    summary = _load_json_object(summary_path)
    if summary.get("passed") is not True or summary.get("failure_count") != 0:
        raise ValueError("provider proof summary did not pass cleanly")
    ran_count = summary.get("ran_count")
    event_count = summary.get("event_count")
    if not isinstance(ran_count, int) or isinstance(ran_count, bool) or ran_count < 1:
        raise ValueError("provider proof summary contains no executed live call")
    if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count < 1:
        raise ValueError("provider proof summary contains no event")
    observed_fingerprint = summary.get("runtime_fingerprint")
    expected_fingerprint = runtime_fingerprint()
    if observed_fingerprint != expected_fingerprint:
        raise ValueError("provider proof was not produced by the current runtime code")
    artifacts = summary.get("artifacts")
    events_value = artifacts.get("events_jsonl") if isinstance(artifacts, Mapping) else None
    if not isinstance(events_value, str) or not events_value:
        raise ValueError("provider proof summary contains no events_jsonl artifact")
    events_path = Path(events_value).expanduser().resolve()
    events = _strict_events(events_path)
    if len(events) != event_count:
        raise ValueError(f"proof event count mismatch: summary={event_count}, artifact={len(events)}")
    current = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    _age_seconds(summary.get("generated_at"), now=current, label="provider proof capture time")
    for event in events:
        _age_seconds(event.timestamp, now=current, label=f"provider proof event {event.event_id} timestamp")
    return summary, events_path, events


def approve_provider_proof(
    summary_path: str | Path,
    output_path: str | Path,
    *,
    reviewer: str,
    review_notes: str,
    approved_capabilities: Sequence[str],
    capture_key_file: str | Path,
    review_key_file: str | Path,
) -> dict[str, Any]:
    reviewer = reviewer.strip()
    review_notes = review_notes.strip()
    requested = tuple(dict.fromkeys(capability.strip() for capability in approved_capabilities if capability.strip()))
    if not reviewer:
        raise ValueError("reviewer must be explicit")
    if not review_notes:
        raise ValueError("review notes must be explicit")
    if not requested:
        raise ValueError("at least one capability must be explicitly approved")
    capture_key = _read_key(capture_key_file)
    review_key = _read_key(review_key_file)
    if _key_id(capture_key) == _key_id(review_key):
        raise ValueError("capture and review proof keys must be distinct")
    summary_target = Path(summary_path).expanduser().resolve()
    capture_attestation = _validate_capture_attestation(summary_target, capture_key)
    summary, events_path, events = _validated_summary(summary_target)
    observed = observed_capabilities(events)
    unsupported = sorted(set(requested) - set(observed))
    if unsupported:
        raise ValueError("capabilities were not observed in exact events: " + ", ".join(unsupported))

    artifact_records = dict(capture_attestation["artifacts"])
    artifact_records.setdefault("events_jsonl", _path_record(events_path))
    manifest = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "proof_id": uuid.uuid4().hex,
        "status": "approved",
        "reviewed_at": _timestamp(),
        "reviewer": reviewer,
        "review_notes": review_notes,
        "runtime_fingerprint": runtime_fingerprint(),
        "git_commit": capture_attestation["git_commit"],
        "capture_key_id": _key_id(capture_key),
        "review_key_id": _key_id(review_key),
        "source_generated_at": summary.get("generated_at"),
        "approved_capabilities": list(requested),
        "observed_capabilities": list(observed),
        "event_count": len(events),
        "observed_total_contributing_tokens": sum(event.event_contributing_tokens for event in events),
        "artifacts": artifact_records,
        "attestation": "separate HMAC capture and review attestations with SHA-256 artifact integrity",
    }
    manifest["review_hmac_sha256"] = _sign(manifest, review_key)
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def validate_provider_proof(
    path: str | Path,
    *,
    max_age_seconds: float,
    capture_key_file: str | Path,
    review_key_file: str | Path,
    now: dt.datetime | None = None,
) -> ProofValidation:
    target = Path(path).expanduser().resolve()
    try:
        manifest = _load_json_object(target)
        review_signature = manifest.pop("review_hmac_sha256", None)
        capture_key = _read_key(capture_key_file)
        review_key = _read_key(review_key_file)
        if _key_id(capture_key) == _key_id(review_key):
            raise ValueError("capture and review proof keys must be distinct")
        if manifest.get("review_key_id") != _key_id(review_key) or not _verify_signature(
            manifest,
            review_signature,
            review_key,
        ):
            raise ValueError("provider proof review signature is invalid")
        if manifest.get("capture_key_id") != _key_id(capture_key):
            raise ValueError("provider proof capture key identity differs")
        if manifest.get("schema_version") != PROOF_SCHEMA_VERSION:
            raise ValueError("unsupported provider proof schema_version")
        if manifest.get("status") != "approved":
            raise ValueError("provider proof is not approved")
        if not isinstance(manifest.get("reviewer"), str) or not manifest["reviewer"].strip():
            raise ValueError("provider proof reviewer is missing")
        if not isinstance(manifest.get("review_notes"), str) or not manifest["review_notes"].strip():
            raise ValueError("provider proof review notes are missing")
        current = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
        review_age = _age_seconds(manifest.get("reviewed_at"), now=current, label="provider proof reviewed_at")
        capture_age = _age_seconds(
            manifest.get("source_generated_at"),
            now=current,
            label="provider proof source_generated_at",
        )
        if review_age > max_age_seconds or capture_age > max_age_seconds:
            raise ValueError(
                f"provider proof is stale: capture={capture_age:.0f}s review={review_age:.0f}s, "
                f"limit {max_age_seconds:g}s"
            )
        if manifest.get("runtime_fingerprint") != runtime_fingerprint():
            raise ValueError("provider proof runtime fingerprint differs from current code")
        artifact_records = manifest.get("artifacts")
        _validate_artifact_records(artifact_records)
        summary_path = Path(artifact_records["source_summary"]["path"])
        capture_attestation = _validate_capture_attestation(summary_path, capture_key)
        if manifest.get("git_commit") != capture_attestation.get("git_commit"):
            raise ValueError("provider proof Git commit changed after capture")
        if manifest.get("artifacts") != capture_attestation.get("artifacts"):
            raise ValueError("provider proof artifact inventory changed after capture")
        _, _, events = _validated_summary(summary_path, now=current)
        observed_capabilities_value = observed_capabilities(events)
        approved = manifest.get("approved_capabilities")
        if not isinstance(approved, list) or not approved or not all(isinstance(item, str) for item in approved):
            raise ValueError("provider proof approved_capabilities is invalid")
        capabilities = tuple(dict.fromkeys(approved))
        if not set(capabilities).issubset(observed_capabilities_value):
            raise ValueError("approved capabilities are no longer supported by proof events")
        if manifest.get("observed_capabilities") != list(observed_capabilities_value):
            raise ValueError("provider proof observed_capabilities changed")
        if manifest.get("event_count") != len(events):
            raise ValueError("provider proof event count changed")
        expected_total = sum(event.event_contributing_tokens for event in events)
        if manifest.get("observed_total_contributing_tokens") != expected_total:
            raise ValueError("provider proof token total changed")
        proof_id = manifest.get("proof_id")
        if not isinstance(proof_id, str) or not proof_id:
            raise ValueError("provider proof id is missing")
        return ProofValidation(str(target), True, f"approved live proof {proof_id[:12]} is intact", capabilities, proof_id)
    except (OSError, ValueError) as exc:
        return ProofValidation(str(target), False, str(exc))


def proof_manifest_paths(directory: str | Path | None) -> list[Path]:
    if directory is None:
        return []
    target = Path(directory).expanduser().resolve()
    if not target.exists():
        return []
    if not target.is_dir():
        raise ValueError(f"provider proof path is not a directory: {target}")
    return sorted(target.glob("*.json"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Approve a successful live provider smoke as hash-bound evidence")
    parser.add_argument("--init-keys", metavar="DIRECTORY")
    parser.add_argument("--summary")
    parser.add_argument("--out")
    parser.add_argument("--reviewer")
    parser.add_argument("--notes")
    parser.add_argument("--approve", action="append", metavar="PROVIDER:SURFACE:CAPABILITY")
    parser.add_argument("--capture-key-file", default=os.environ.get("TRACKER_PROOF_CAPTURE_KEY_FILE"))
    parser.add_argument("--review-key-file", default=os.environ.get("TRACKER_PROOF_REVIEW_KEY_FILE"))
    args = parser.parse_args(argv)
    if args.init_keys:
        try:
            paths = initialize_proof_keys(args.init_keys)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"initialized": True, **paths}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    required = {
        "--summary": args.summary,
        "--out": args.out,
        "--reviewer": args.reviewer,
        "--notes": args.notes,
        "--approve": args.approve,
        "--capture-key-file": args.capture_key_file,
        "--review-key-file": args.review_key_file,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("approval requires " + ", ".join(missing))
    try:
        manifest = approve_provider_proof(
            args.summary,
            args.out,
            reviewer=args.reviewer,
            review_notes=args.notes,
            approved_capabilities=args.approve,
            capture_key_file=args.capture_key_file,
            review_key_file=args.review_key_file,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "approved": True,
                "proof_id": manifest["proof_id"],
                "capabilities": manifest["approved_capabilities"],
                "output": str(Path(args.out).expanduser().resolve()),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PROOF_SCHEMA_VERSION",
    "ProofValidation",
    "approve_provider_proof",
    "initialize_proof_keys",
    "observed_capabilities",
    "proof_manifest_paths",
    "validate_provider_proof",
    "write_capture_attestation",
]
