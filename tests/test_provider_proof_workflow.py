"""Reviewed live proofs must be explicit, hash-bound, current, and capability-scoped."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.ops.provider_proof import (  # noqa: E402
    approve_provider_proof,
    initialize_proof_keys,
    proof_manifest_paths,
    source_identity,
    validate_provider_proof,
    write_capture_attestation,
)
from tracker.ops.release_readiness import provider_proof_checks, provider_proof_manifest_checks  # noqa: E402
from tracker.ops.runtime_fingerprint import runtime_fingerprint  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
root = (Path.cwd() / f".test_provider_proof_{uuid.uuid4().hex}").resolve()
root.mkdir(parents=True, exist_ok=False)
try:
    captured_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
    captured_at_text = captured_at.isoformat().replace("+00:00", "Z")
    run_dir = root / "run"
    run_dir.mkdir()
    events_path = run_dir / "events.jsonl"
    event = TokenEvent(
        event_id="live-response-stream",
        request_correlation_id="live-response-stream-request",
        trace_id="live-response-stream-trace",
        span_id="live-response-stream-span",
        provider="azure_openai",
        model="gpt-test",
        api_surface="responses",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                100,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_STREAM_FINAL,
                Additivity.TOTAL_CONTRIBUTING,
            ),
            TokenQuantity(
                TokenType.CACHED_INPUT,
                40,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_STREAM_FINAL,
                Additivity.SUBTOTAL_OF,
                subtotal_of=TokenType.INPUT.value,
            ),
            TokenQuantity(
                TokenType.OUTPUT,
                25,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_STREAM_FINAL,
                Additivity.TOTAL_CONTRIBUTING,
            ),
        ],
        provider_total_tokens=125,
        timestamp=captured_at_text,
        observation={
            "authoritative": True,
            "status": "complete",
            "duration_ms": 42.0,
            "http_status": 200,
            "cloud_provider": "azure",
            "deployment": "gpt-test",
            "provider_response_id": "resp-live-proof",
        },
    )
    FileRepository(events_path).append(event)
    config_path = run_dir / "config_redacted.json"
    config_path.write_text('{"api_key":"present"}', encoding="utf-8")
    summary_path = run_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "passed": True,
                "ran_count": 1,
                "skipped_count": 0,
                "failure_count": 0,
                "event_count": 1,
                "observed_total_contributing_tokens": 125,
                "generated_at": captured_at_text,
                "runtime_fingerprint": runtime_fingerprint(),
                "artifacts": {"events_jsonl": str(events_path), "config": str(config_path)},
            }
        ),
        encoding="utf-8",
    )
    key_paths = initialize_proof_keys(root / "keys")
    write_capture_attestation(
        summary_path,
        key_paths["capture_key"],
        harness="unit-live-smoke",
        identity={"git_commit": source_identity()["git_commit"], "git_worktree_clean": True},
    )
    proof_dir = root / "approved"
    proof_path = proof_dir / "azure-responses-stream.json"
    manifest = approve_provider_proof(
        summary_path,
        proof_path,
        reviewer="delivery-owner",
        review_notes="Terminal provider usage and reconciliation inspected against the redacted stream artifact.",
        approved_capabilities=["azure_openai:responses:stream"],
        capture_key_file=key_paths["capture_key"],
        review_key_file=key_paths["review_key"],
    )
    check(
        manifest["approved_capabilities"] == ["azure_openai:responses:stream"],
        "reviewer approves only the explicitly requested observed capability",
    )
    check(
        set(manifest["observed_capabilities"])
        == {
            "azure_openai:responses:cache",
            "azure_openai:responses:stream",
            "azure_openai:responses:usage",
        },
        "usage, stream, and cache capabilities are derived from exact events",
    )
    reviewed_at = dt.datetime.fromisoformat(str(manifest["reviewed_at"]).replace("Z", "+00:00"))
    validation_now = reviewed_at + dt.timedelta(minutes=30)
    validation = validate_provider_proof(
        proof_path,
        max_age_seconds=3600,
        capture_key_file=key_paths["capture_key"],
        review_key_file=key_paths["review_key"],
        now=validation_now,
    )
    check(validation.valid, f"intact reviewed proof validates ({validation.detail})")
    checks, validations = provider_proof_manifest_checks(
        proof_dir,
        max_age_seconds=3600,
        capture_key_file=key_paths["capture_key"],
        review_key_file=key_paths["review_key"],
        now=validation_now,
    )
    check(not any(item.failed for item in checks), "release evidence check accepts the intact manifest")
    capability_checks = provider_proof_checks(
        ["azure_openai:responses:stream", "vertex_ai:embeddings:usage"],
        reviewed_capabilities=[capability for item in validations for capability in item.capabilities if item.valid],
    )
    by_name = {item.name: item for item in capability_checks}
    check(
        not by_name["provider-proof:azure_openai:responses:stream"].failed,
        "reviewed live proof closes the exact Azure Responses stream requirement",
    )
    check(
        by_name["provider-proof:vertex_ai:embeddings:usage"].failed,
        "one reviewed proof cannot promote another provider or surface",
    )
    check(proof_manifest_paths(proof_dir) == [proof_path.resolve()], "proof directory discovery is deterministic")

    try:
        approve_provider_proof(
            summary_path,
            proof_dir / "invalid.json",
            reviewer="delivery-owner",
            review_notes="Unsupported claim test.",
            approved_capabilities=["bedrock:converse:stream"],
            capture_key_file=key_paths["capture_key"],
            review_key_file=key_paths["review_key"],
        )
    except ValueError:
        unsupported_rejected = True
    else:
        unsupported_rejected = False
    check(unsupported_rejected, "a reviewer cannot approve a capability absent from exact proof events")

    stale = validate_provider_proof(
        proof_path,
        max_age_seconds=60,
        capture_key_file=key_paths["capture_key"],
        review_key_file=key_paths["review_key"],
        now=reviewed_at + dt.timedelta(minutes=2),
    )
    check(not stale.valid and "stale" in stale.detail, "stale provider proof fails closed")

    config_path.write_text('{"api_key":"changed"}', encoding="utf-8")
    tampered = validate_provider_proof(
        proof_path,
        max_age_seconds=3600,
        capture_key_file=key_paths["capture_key"],
        review_key_file=key_paths["review_key"],
        now=validation_now,
    )
    check(
        not tampered.valid and "hash mismatch" in tampered.detail,
        f"artifact tampering invalidates approval ({tampered.detail})",
    )
finally:
    shutil.rmtree(root, ignore_errors=True)

sys.exit(check.report("RESULT test_provider_proof_workflow"))
