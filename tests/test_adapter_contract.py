"""Phase 4 — adapter contract: BaseAPISurfaceAdapter + NormalizedUsage.

Run: python tests/test_adapter_contract.py

Pins the contract WITHOUT any provider logic (that arrives in Phase 5 with real payloads):
  - the base is abstract until response and stream extraction are implemented;
  - common estimation/error/total behavior is inherited and may be overridden;
  - the base centralizes assign_additivity via the Phase 3 table (INV-4), so every adapter
    agrees and additivity is never inferred from the type string;
  - NormalizedUsage carries assigned precision/additivity/subtotal_of, but exposes NO
    derived field and NO supersession — adapters assign, they never derive or supersede.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


# --- the base is abstract: cannot be instantiated directly ---
abstract_ok = False
try:
    BaseAPISurfaceAdapter()  # type: ignore[abstract]
except TypeError:
    abstract_ok = True
check(abstract_ok, "BaseAPISurfaceAdapter cannot be instantiated (abstract)")


# --- a subclass that forgets a method is still abstract ---
class _Incomplete(BaseAPISurfaceAdapter):
    provider = "openai"
    api_surface = "responses"

    def count_input_tokens(self, request):  # noqa: D401
        return 0


incomplete_ok = False
try:
    _Incomplete()  # type: ignore[abstract]
except TypeError:
    incomplete_ok = True
check(incomplete_ok, "a subclass missing methods stays abstract")


# --- a complete minimal adapter (no real provider logic) instantiates ---
class _FakeOpenAI(BaseAPISurfaceAdapter):
    provider = "openai"
    api_surface = "responses"

    def count_input_tokens(self, request):
        return 0

    def extract_usage_from_response(self, response):
        return NormalizedUsage(provider=self.provider, api_surface=self.api_surface)

    def extract_usage_from_stream_event(self, event):
        return None

    def estimate_partial_output_tokens(self, accumulated_text):
        return 0

    def reconcile_total(self, quantities, raw_total):
        return raw_total

    def classify_error(self, exc):
        return "normalization_error"


adapter = _FakeOpenAI()
check(isinstance(adapter, BaseAPISurfaceAdapter), "complete subclass instantiates")

# --- assign_additivity is inherited from the base and uses the Phase 3 table (INV-4) ---
add_in, sub_in = adapter.assign_additivity(TokenType.INPUT)
add_cached, sub_cached = adapter.assign_additivity(TokenType.CACHED_INPUT)
add_reason, sub_reason = adapter.assign_additivity(TokenType.REASONING)
check(add_in == Additivity.TOTAL_CONTRIBUTING and sub_in is None, "input -> total_contributing (via table)")
check(
    add_cached == Additivity.SUBTOTAL_OF and sub_cached == "input",
    "cached_input -> subtotal_of input (via table)",
)
check(
    add_reason == Additivity.SUBTOTAL_OF and sub_reason == "output",
    "reasoning -> subtotal_of output (via table)",
)

# --- the base helper builds a quantity with additivity already assigned ---
q = adapter.build_quantity(TokenType.CACHED_INPUT, 800, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE)
check(q.additivity == Additivity.SUBTOTAL_OF and q.subtotal_of == "input", "build_quantity assigns additivity")
check(q.quantity_in_total == 0, "an adapter-built subtotal contributes 0 (derivation untouched)")

# --- NormalizedUsage: assigned facts only, NO derived field, NO supersession ---
usage = NormalizedUsage(provider="openai", api_surface="responses", quantities=[q], provider_total_tokens=1300)
check(usage.quantities == [q] and usage.provider_total_tokens == 1300, "NormalizedUsage stores assigned usage")
forbidden_attrs = ["superseded", "superseded_by", "event_contributing_tokens", "quantity_in_total", "included_in_total"]
leaked = [a for a in forbidden_attrs if hasattr(usage, a)]
check(leaked == [], f"NormalizedUsage exposes no derived/supersession field (leaked: {leaked})")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
