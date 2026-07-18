"""Regression — the generic fallback must fail CLOSED even for a KNOWN provider.

Run: python tests/test_fallback_known_provider_unverified.py

The generic fallback exists for capture paths that hit a (provider, surface) pair WITHOUT a
dedicated, tested adapter. Its documented contract (see generic_fallback_adapter.py and
test_proxy_unknown_provider_fallback.py) is "open capture, CLOSED counting": every captured
quantity is UNVERIFIED, contributes 0, and raises ``unverified_additivity`` until a dedicated
adapter encodes and TESTS that surface's real additivity truth.

That contract held only because the central additivity table happened to fail closed for the
*unknown providers* the fallback was tested with (groq). But the table is keyed by provider and
IGNORES the surface, so a KNOWN provider on an UNKNOWN/unverified surface (e.g. a future
``openai/realtime``) resolved to TOTAL_CONTRIBUTING and was silently counted at full confidence
with NO caution flag — the opposite of the documented fail-closed behavior, and a violation of
the tracker's core discipline (count only what a payload+test PROVE for that surface).

This pins the honest behavior: the fallback's counting is closed regardless of provider.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters.generic_fallback_adapter import (
    GenericFallbackAdapter,
)  # noqa: E402
from tracker.adapters.registry import create_adapter_with_fallback  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()

# A KNOWN provider (openai) but an UNKNOWN/unverified surface: no dedicated adapter exists, so
# create_adapter_with_fallback hands back the generic fallback. This is exactly the path where
# we have NOT proven the surface's usage semantics with a real payload + test.
fb = create_adapter_with_fallback("openai", "realtime")
check(
    isinstance(fb, GenericFallbackAdapter),
    "known provider on an unverified surface -> generic fallback",
)
check(
    fb.provider == "openai" and fb.api_surface == "realtime",
    "fallback stamps the real provider/surface",
)

payload = {
    "model": "gpt-4o-realtime",
    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
}

usage = fb.extract_usage_from_response(payload)
check(
    {q.token_type for q in usage.quantities} == {TokenType.INPUT, TokenType.OUTPUT},
    "the real counts are still captured (open capture is unchanged)",
)
check(
    all(q.additivity == Additivity.UNVERIFIED for q in usage.quantities),
    "CLOSED counting: every captured quantity is UNVERIFIED even for a known provider",
)
check(
    usage.provider_total_tokens == 150,
    "the raw provider total is still preserved (never summed)",
)

ev = normalize(payload, fb, context=new_trace())
check(
    ev.event_contributing_tokens == 0,
    "an unverified surface contributes 0 to totals until a dedicated adapter proves it",
)
check(
    "unverified_additivity" in ev.data_quality_flags,
    "and the unverified_additivity caution flag is raised",
)

# A genuinely unknown provider must stay closed too (unchanged behavior, guarded here).
fb2 = create_adapter_with_fallback("groq", "chat_completions")
ev2 = normalize(
    {"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}},
    fb2,
    context=new_trace(),
)
check(
    ev2.event_contributing_tokens == 0 and "unverified_additivity" in ev2.data_quality_flags,
    "unknown provider still closed",
)

sys.exit(check.report("RESULT test_fallback_known_provider_unverified"))
