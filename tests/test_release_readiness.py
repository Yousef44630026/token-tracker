"""Release gate must reject claims that exceed real provider/dashboard evidence."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.release_readiness import (  # noqa: E402
    dashboard_evidence_checks,
    main,
    provider_proof_checks,
    run_release_checks,
)

check = make_checker()
root = Path(tempfile.mkdtemp(prefix="tracker-release-readiness-"))
evidence = root / "dashboard-refresh.json"
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
            "quality_status": "clean",
            "volume_status": "ok",
            "data_row_count": 1000,
        },
    }
    evidence.write_text(json.dumps(payload), encoding="utf-8")


write_evidence()
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
    required_quality_status="clean",
    now=now,
)
check(not any(item.failed for item in dashboard_checks), "fresh, covered dashboard evidence passes")

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

shutil.rmtree(root, ignore_errors=True)
sys.exit(check.report("RESULT test_release_readiness"))
