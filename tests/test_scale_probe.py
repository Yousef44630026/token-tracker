"""Scale evidence is isolated, reconciled, bounded, and machine-readable."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.scale_probe import run_probe  # noqa: E402

check = make_checker()
configured_workspace = os.environ.get("TRACKER_TEST_WORKSPACE")
work_dir = Path(configured_workspace) / "scale-probe" if configured_workspace else None

report = run_probe(
    event_count=25,
    batch_size=7,
    max_projection_seconds=30,
    max_dashboard_seconds=30,
    max_peak_memory_mb=256,
    work_dir=work_dir,
)
check(report.passed, f"isolated scale probe passes its explicit budgets ({report.failures})")
check(report.event_count == report.effective_event_count == 25, "every source event survives effective projection")
check(report.total_tokens == 375, "projection and dashboard reconcile to the exact synthetic total")
check(report.store_bytes > 0, "probe measures a real temporary JSONL store")
check(report.projection_seconds >= 0 and report.dashboard_seconds >= 0, "timings are emitted as non-negative evidence")
check(report.memory_probe_seconds >= 0, "instrumented memory pass is reported separately from latency")
check(report.peak_memory_mb > 0, "peak memory is measured")

try:
    run_probe(event_count=0)
except ValueError:
    invalid_rejected = True
else:
    invalid_rejected = False
check(invalid_rejected, "invalid scale claims fail closed")

sys.exit(check.report("RESULT test_scale_probe"))
