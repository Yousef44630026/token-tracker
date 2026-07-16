"""The normalizer — the single assembly point (keystone).

``normalize(response, adapter)`` is the one call that turns a raw provider response into a
stored ``TokenEvent``. It is the glue the rest of the system is built around:

  1. runs the adapter to get a NormalizedUsage (assigned quantities + raw provider total);
  2. takes identity/context from the propagation layer (the active TraceContext, or one
     passed explicitly), so the event attaches to the right trace/span (INV-5);
  3. reconciles the provider total via the adapter, then applies the normalizer-owned
     data-quality flags (unverified_additivity / unknown_quantity_present /
     provider_total_mismatch), merged with the adapter's own flags (raw_usage_missing) and
     any extra flags, de-duplicated;
  4. NEVER raises into the caller: if the adapter blows up, OR if a value the adapter passed
     through unvalidated fails the model's own validation (e.g. a non-integer provider total
     from a corrupted response), the result is a ``normalization_error`` event with no
     quantities, not a crash. The safety net wraps the FULL assembly, not just the adapter
     call, precisely because an adapter is allowed to pass raw field values through without
     type-checking them itself — the model's validation is the backstop, and it must never
     be allowed to escape as an unhandled exception.

What it deliberately does NOT do: compute derived totals (INV-2 — those stay @property), set
supersession (INV-5 — reconciler/stream tracker), or persist anything (the collector/repo do).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tracker.context.propagation import TraceContext, current, current_flags, new_trace
from tracker.models.token_event import TokenEvent
from tracker.normalization.event_builder import build_event
from tracker.observability.observation import Observation

if TYPE_CHECKING:
    from tracker.adapters.base import BaseAPISurfaceAdapter


def normalize(
    response: Any,
    adapter: BaseAPISurfaceAdapter,
    *,
    context: TraceContext | None = None,
    event_id: str | None = None,
    request_hash: str | None = None,
    response_hash: str | None = None,
    timestamp: str | None = None,
    extra_flags: list[str] | None = None,
    observation: dict[str, Any] | None = None,
) -> TokenEvent:
    """Assemble one TokenEvent from a raw provider ``response`` using ``adapter``."""
    ambient = current()
    ctx = context or ambient or new_trace()
    propagation_flags = current_flags() if ambient is not None and ctx is ambient else ()
    extra = [*propagation_flags, *(extra_flags or [])]

    def _error_event(exc: Exception) -> TokenEvent:
        error_flag = adapter.classify_error(exc)
        return build_event(
            event_id=event_id,
            context=ctx,
            provider=getattr(adapter, "provider", None),
            api_surface=getattr(adapter, "api_surface", None),
            model=None,
            quantities=[],
            provider_total_tokens=None,
            leading_flags=[error_flag],
            trailing_flags=extra,
            request_hash=request_hash,
            response_hash=response_hash,
            timestamp=timestamp,
            observation=(
                observation
                if observation is not None
                else Observation(
                    authoritative=False,
                    status="failed",
                    provider_error_code=error_flag,
                )
            ),
        )

    # The WHOLE assembly is defensive, not just the adapter call: an adapter is allowed to
    # pass a raw field value through without type-checking it (e.g. provider_total_tokens),
    # relying on the model's own validation as the backstop (INV-1) — so a failure surfacing
    # only once build_event()/TokenEvent construction runs must be caught here too, or that
    # backstop's exception would itself escape unhandled.
    try:
        # 1) run the adapter to get assigned quantities + the raw provider total
        usage = adapter.extract_usage_from_response(response)

        # 2) reconcile the raw provider total (still raw data; never summed across events)
        provider_total = adapter.reconcile_total(usage.quantities, usage.provider_total_tokens)

        # 3) drift defense: an adapter that extracted NO quantities read nothing usable —
        # whether the usage object was absent or present-but-unrecognized (a renamed/changed
        # API). Either way the usage is missing, so flag it rather than emit a silent empty
        # event.
        leading = list(usage.data_quality_flags)
        if not usage.quantities and "raw_usage_missing" not in leading:
            leading.append("raw_usage_missing")

        return build_event(
            event_id=event_id,
            context=ctx,
            provider=usage.provider,
            api_surface=usage.api_surface,
            model=usage.model,
            quantities=usage.quantities,
            provider_total_tokens=provider_total,
            leading_flags=leading,
            trailing_flags=extra,
            request_hash=request_hash,
            response_hash=response_hash,
            timestamp=timestamp,
            observation=(observation if observation is not None else Observation(authoritative=True, status="complete")),
        )
    except Exception as exc:  # noqa: BLE001 — by design: turn it into a flagged event
        return _error_event(exc)
