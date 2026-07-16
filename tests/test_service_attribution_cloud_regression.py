"""Regression — direct Gemini and Vertex AI must not be attributed to the same "cloud".

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_service_attribution_cloud_regression.py

Found during a rigorous logic/relevance review of tracker/analytics/service_attribution.py:
`_CLOUD_BY_PROVIDER` mapped both "vertex_ai" and "gemini" to "gcp". Direct Gemini (Google AI
Studio, API-key access — the surface this project's own real captures used) is a genuinely
different billing/auth surface from Vertex AI, and is frequently not tied to any GCP project at
all. Fixed by removing the "gemini" entry: Vertex AI still correctly attributes to "gcp"
(a real, reconcilable GCP billing surface); direct Gemini now falls through to its own provider
name ("gemini") instead of being silently merged into a cloud bucket it doesn't belong in.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.analytics.service_attribution import build_service_attribution  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(qty):
    return TokenQuantity(TokenType.INPUT, qty, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING)


trace = Trace(trace_id="cloud-attribution-regression")
trace.add_event(
    TokenEvent(
        event_id="gemini-direct",
        request_correlation_id="r1",
        trace_id=trace.trace_id,
        span_id="s1",
        provider="gemini",
        model="gemini-2.5-flash",
        api_surface="generate_content",
        quantities=[q(100)],
        observation={"authoritative": True},
    )
)
trace.add_event(
    TokenEvent(
        event_id="vertex-ai",
        request_correlation_id="r2",
        trace_id=trace.trace_id,
        span_id="s2",
        provider="vertex_ai",
        model="gemini-2.5-flash",
        api_surface="generate_content",
        quantities=[q(200)],
        observation={"authoritative": True},
    )
)

rows = build_service_attribution(trace)["rows"]
gemini_row = next(row for row in rows if row["provider"] == "gemini")
vertex_row = next(row for row in rows if row["provider"] == "vertex_ai")

check(vertex_row["cloud_provider"] == "gcp", "Vertex AI still correctly attributes to a real, reconcilable GCP billing surface")
check(
    gemini_row["cloud_provider"] != "gcp",
    f"FIXED: direct Gemini is no longer merged into 'gcp' (got {gemini_row['cloud_provider']!r})",
)
check(
    gemini_row["cloud_provider"] == "gemini",
    f"direct Gemini falls through to its own provider name, not a fabricated cloud (got {gemini_row['cloud_provider']!r})",
)
check(
    gemini_row["cloud_provider"] != vertex_row["cloud_provider"],
    "the two genuinely different billing surfaces are no longer indistinguishable in this report",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
