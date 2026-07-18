"""Extra — precision classifier is a TOTAL mapping over every usage source (INV-3 / INV-6).

Run: python tests/test_precision_classifier_edges.py

Pins classify_precision for every UsageSource member, both with a known quantity and with
None (which is always UNKNOWN). Guards against a new source silently defaulting wrong.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.classification.precision_classifier import classify_precision  # noqa: E402
from tracker.models.enums import PrecisionLevel, UsageSource  # noqa: E402

_failures = 0

EXPECTED = {
    UsageSource.PROVIDER_RESPONSE: PrecisionLevel.EXACT,
    UsageSource.PROVIDER_STREAM_FINAL: PrecisionLevel.EXACT,
    UsageSource.PROVIDER_STREAM_PARTIAL: PrecisionLevel.ESTIMATE,
    UsageSource.PARTIAL_STREAM_TOKENIZER: PrecisionLevel.ESTIMATE,
    UsageSource.LOCAL_TOKENIZER: PrecisionLevel.ESTIMATE,
    UsageSource.HISTORICAL_FORECAST: PrecisionLevel.ESTIMATE,
    UsageSource.NONE: PrecisionLevel.UNKNOWN,
}


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# the mapping is total: every enum member is covered here
check(set(EXPECTED) == set(UsageSource), "every UsageSource member is covered by the test")

for source, expected in EXPECTED.items():
    # known quantity -> the source's precision
    check(classify_precision(source, 100) == expected, f"{source.value} + known -> {expected.value}")
    # None quantity -> always UNKNOWN, regardless of source (INV-6)
    check(classify_precision(source, None) == PrecisionLevel.UNKNOWN, f"{source.value} + None -> unknown")

# a zero quantity is still a known measurement (0 is not None)
check(classify_precision(UsageSource.PROVIDER_RESPONSE, 0) == PrecisionLevel.EXACT, "0 tokens is exact, not unknown")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
