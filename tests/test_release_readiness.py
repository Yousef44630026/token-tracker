"""Release gate must reject claims that exceed real provider/dashboard evidence."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.release_readiness import (  # noqa: E402
    dashboard_evidence_checks,
    main,
    operational_evidence_checks,
    provider_proof_checks,
    run_release_checks,
    scale_evidence_checks,
)
from tracker.ops.runtime_fingerprint import runtime_fingerprint  # noqa: E402

check = make_checker()
root = (Path.cwd() / f".test_release_readiness_{uuid.uuid4().hex}").resolve()
root.mkdir(parents=True, exist_ok=False)
evidence = root / "dashboard-refresh.json"
scale_evidence = root / "scale-probe.json"
now = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.UTC)


def write_evidence(*, timestamp: str = "2026-07-19T11:59:00Z", report: dict | None = None) -> None:
    payload = {
        "timestamp": timestamp,
        "status": "ok",
        "exit_code": 0,
        "report": report
        or {
            "pricing_coverage": 0.99,
            "latency_coverage": 0.98,
            "instrumented_latency_coverage": 0.99,
            "quality_status": "clean",
            "volume_status": "ok",
            "data_row_count": 1000,
        },
    }
    evidence.write_text(json.dumps(payload), encoding="utf-8")


write_evidence()
scale_evidence.write_text(
    json.dumps(
        {
            "generated_at": "2026-07-19T11:59:30Z",
            "passed": True,
            "failures": [],
            "event_count": 50_000,
            "effective_event_count": 50_000,
            "total_tokens": 750_000,
        }
    ),
    encoding="utf-8",
)

collector_evidence = root / "collector-soak.json"
collector_evidence.write_text(
    json.dumps(
        {
            "evidence_type": "collector_soak",
            "runtime_fingerprint": runtime_fingerprint(),
            "passed": True,
            "ended_at": "2026-07-19T11:59:30Z",
            "requested_duration_seconds": 259_200,
            "wall_elapsed_seconds": 259_100,
            "failed_samples": 0,
            "uptime_ratio": 1.0,
            "sampling": {"complete": True, "sample_gap_count": 0},
            "store_integrity": {"verified": True, "prefix_unchanged": True},
        }
    ),
    encoding="utf-8",
)
collector_checks = operational_evidence_checks(
    collector_evidence,
    evidence_type="collector_soak",
    max_age_seconds=3600,
    min_duration_seconds=259_200,
    now=now,
)
check(not any(item.failed for item in collector_checks), "fresh 72-hour runtime-bound collector soak passes")

recovery_evidence = root / "recovery.json"
required_recovery_checks = (
    "source_validation",
    "backup_integrity",
    "archive_first_retention",
    "restore_integrity",
    "duplicate_recovery",
    "readability",
)
recovery_evidence.write_text(
    json.dumps(
        {
            "evidence_type": "recovery_drill",
            "runtime_fingerprint": runtime_fingerprint(),
            "passed": True,
            "timestamp": "2026-07-19T11:59:30Z",
            "snapshot_events": 100,
            "baseline_sha256": "a" * 64,
            "checks": [{"name": name, "passed": True} for name in required_recovery_checks],
        }
    ),
    encoding="utf-8",
)
recovery_checks = operational_evidence_checks(
    recovery_evidence,
    evidence_type="recovery_drill",
    max_age_seconds=3600,
    required_checks=required_recovery_checks,
    now=now,
)
check(not any(item.failed for item in recovery_checks), "complete runtime-bound recovery drill passes")

billing_evidence = root / "billing.json"
required_billing_checks = ("external_statement_hashed", "scope_matched", "token_variance_within_tolerance")
billing_evidence.write_text(
    json.dumps(
        {
            "evidence_type": "billing_reconciliation",
            "runtime_fingerprint": runtime_fingerprint(),
            "passed": True,
            "generated_at": "2026-07-19T11:59:30Z",
            "scope_start": "2026-07-01T00:00:00Z",
            "scope_end": "2026-07-19T00:00:00Z",
            "ledger_sha256": "b" * 64,
            "external_statement_sha256": "c" * 64,
            "absolute_token_variance": 0,
            "token_variance_tolerance": 0,
            "checks": [{"name": name, "passed": True} for name in required_billing_checks],
        }
    ),
    encoding="utf-8",
)
billing_checks = operational_evidence_checks(
    billing_evidence,
    evidence_type="billing_reconciliation",
    max_age_seconds=3600,
    required_checks=required_billing_checks,
    now=now,
)
check(not any(item.failed for item in billing_checks), "hash-bound zero-variance billing reconciliation passes")

wrong_runtime = json.loads(collector_evidence.read_text(encoding="utf-8"))
wrong_runtime["runtime_fingerprint"] = "0" * 64
collector_evidence.write_text(json.dumps(wrong_runtime), encoding="utf-8")
wrong_runtime_checks = operational_evidence_checks(
    collector_evidence,
    evidence_type="collector_soak",
    max_age_seconds=3600,
    min_duration_seconds=259_200,
    now=now,
)
check(
    any(item.name == "collector_soak-runtime" and item.failed for item in wrong_runtime_checks),
    "stale code cannot reuse a soak",
)
provider_checks = provider_proof_checks(
    [
        "azure_openai:chat_completions:stream",
        "vertex_ai:embeddings:usage",
        "bedrock:embeddings:cohere_response_token_count",
    ]
)
provider_by_name = {item.name: item for item in provider_checks}
check(
    not provider_by_name["provider-proof:azure_openai:chat_completions:stream"].failed,
    "real Azure stream proof satisfies the release requirement",
)
check(
    provider_by_name["provider-proof:vertex_ai:embeddings:usage"].failed,
    "simulated Vertex evidence cannot pass a production gate",
)
check(
    provider_by_name["provider-proof:bedrock:embeddings:cohere_response_token_count"].failed,
    "explicitly unsupported provider counters cannot pass a production gate",
)

dashboard_checks = dashboard_evidence_checks(
    evidence,
    max_age_seconds=300,
    min_pricing_coverage=0.95,
    min_latency_coverage=0.95,
    min_instrumented_latency_coverage=0.95,
    required_quality_status="clean",
    now=now,
)
check(not any(item.failed for item in dashboard_checks), "fresh, covered dashboard evidence passes")
scale_checks = scale_evidence_checks(scale_evidence, max_age_seconds=300, min_event_count=50_000, now=now)
check(not any(item.failed for item in scale_checks), "fresh reconciled scale evidence passes")

undersized_scale = scale_evidence_checks(scale_evidence, max_age_seconds=300, min_event_count=100_000, now=now)
check(
    next(item for item in undersized_scale if item.name == "scale-volume").failed,
    "an undersized benchmark cannot satisfy a larger release claim",
)

stale_checks = dashboard_evidence_checks(
    evidence,
    max_age_seconds=30,
    now=now,
)
check(
    next(item for item in stale_checks if item.name == "dashboard-freshness").failed,
    "stale evidence fails instead of relying on an old green refresh",
)

write_evidence(
    report={
        "pricing_coverage": 0.0,
        "latency_coverage": None,
        "instrumented_latency_coverage": None,
        "quality_status": "warning",
        "volume_status": "warning",
        "data_row_count": 150000,
    }
)
strict_checks = run_release_checks(
    provider_requirements=["azure_openai:responses:stream"],
    dashboard_evidence=str(evidence),
    max_dashboard_age_seconds=300,
    min_pricing_coverage=0.95,
    min_latency_coverage=0.95,
    min_instrumented_latency_coverage=0.95,
    required_quality_status="clean",
    now=now,
)
check(any(item.failed for item in strict_checks), "unproven streaming and incomplete dashboard coverage fail together")
check(any(item.warned for item in strict_checks), "high workbook volume remains an explicit release warning")

write_evidence()
buffer = StringIO()
with redirect_stdout(buffer):
    exit_code = main(
        [
            "--dashboard-evidence",
            str(evidence),
            "--max-dashboard-age-seconds",
            "999999999",
            "--min-pricing-coverage",
            "0.95",
            "--min-latency-coverage",
            "0.95",
            "--require-quality-status",
            "clean",
            "--require-proven",
            "vertex_ai:embeddings:usage",
            "--json",
        ]
    )
check(exit_code == 1, "CLI exits red when a required provider capability is not proven")
cli_payload = json.loads(buffer.getvalue())
check(cli_payload["failure_count"] == 1, "CLI JSON quantifies the exact release failure count")

batch = (Path(__file__).parents[1] / "scripts" / "tt-release-gate.cmd").read_text(encoding="utf-8")
check("tt-check.cmd" in batch and "tt-doctor.cmd" in batch, "Windows release gate includes code and runtime checks")
check("vertex_ai:embeddings:usage" in batch, "multi-cloud gate refuses to omit the unproven Vertex surface")
check("--min-pricing-coverage 0.95" in batch, "multi-cloud gate enforces dashboard cost coverage")
check(
    "--min-instrumented-latency-coverage 0.95" in batch,
    "multi-cloud gate measures latency only where the source can capture it",
)
check("--min-scale-events 50000" in batch, "multi-cloud gate requires a recent 50k-event scale proof")
check(
    "--collector-soak-evidence" in batch
    and "--recovery-evidence" in batch
    and "--billing-evidence" in batch,
    "multi-cloud gate requires soak, recovery, and billing evidence",
)

shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_release_readiness"))
