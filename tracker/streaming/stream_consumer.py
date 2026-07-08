"""Stream consumer — drive a StreamTracker from a provider's stream events. (streaming)

This is the glue between a provider's streaming SDK and the tracker. It iterates the provider's
stream events, uses the adapter to pull usage out of each (some providers split it: Anthropic
sends input in message_start and output in message_delta), feeds text deltas for partial
estimation, and emits the terminal TokenEvent:

  - clean completion (the provider's final usage was seen)  -> complete()  [EXACT]
  - the stream ends / errors without a final usage          -> interrupt() [ESTIMATE from text]

Tracking never breaks the caller: a stream that raises mid-iteration, OR whose final usage
values are malformed enough that building the terminal TokenEvent itself fails (e.g. a
non-integer provider total surviving an adapter's unvalidated passthrough), is treated as an
interruption, not propagated. The defensive boundary wraps the FULL assembly — ingestion AND
terminal-event construction — for the same reason normalize()'s does (see normalizer.py): an
adapter is allowed to pass a raw field through without type-checking it, so the model's own
validation is the backstop, and that backstop's exception must never escape uncaught either.

``text_extractor`` is an optional provider-specific callable ``event -> Optional[str]`` that
returns the text delta of an event (used only to estimate an interrupted output).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tracker.context.propagation import TraceContext
from tracker.models.enums import TokenType
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.streaming.stream_tracker import StreamTracker


def consume_stream(
    events: Iterable[Any],
    adapter: Any,
    *,
    context: TraceContext,
    text_extractor: Callable[[Any], str | None] | None = None,
    model: str | None = None,
) -> TokenEvent:
    """Consume a provider stream via ``adapter`` and return the terminal TokenEvent."""
    tracker = StreamTracker.from_context(context, provider=adapter.provider, api_surface=adapter.api_surface, model=model)
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_total: int | None = None
    final_model: str | None = model
    final_quantities: dict[tuple[TokenType, str | None, str | None], TokenQuantity] = {}
    saw_output = False

    try:
        for event in events:
            if text_extractor is not None:
                delta = text_extractor(event)
                if delta:
                    tracker.feed(delta)
            usage = adapter.extract_usage_from_stream_event(event)
            if usage is None:
                continue
            if usage.model is not None:
                final_model = usage.model
            for quantity in usage.quantities:
                if quantity.quantity is None:
                    continue
                key = (quantity.token_type, quantity.token_role, quantity.subtotal_of)
                final_quantities[key] = quantity
                if quantity.token_type == TokenType.INPUT:
                    input_tokens = quantity.quantity
                    tracker.observe_usage(input_tokens=quantity.quantity)
                elif quantity.token_type == TokenType.OUTPUT:
                    output_tokens = quantity.quantity
                    saw_output = True
                    tracker.observe_usage(output_tokens=quantity.quantity)
            if usage.provider_total_tokens is not None:
                provider_total = usage.provider_total_tokens

        if not saw_output:
            # keep whatever real usage was already received (e.g. Anthropic's exact input from
            # message_start) — an interrupt must never throw away known tokens. interrupt()
            # reads the monotonic values recorded via observe_usage above.
            return tracker.interrupt()
        if final_quantities:
            return tracker.complete_with_quantities(
                quantities=list(final_quantities.values()),
                provider_total_tokens=provider_total,
                model=final_model,
            )
        return tracker.complete(
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            provider_total_tokens=provider_total,
        )
    except Exception:  # noqa: BLE001 — a broken stream (or malformed terminal usage) is an
        # interruption, never a crash — see the module docstring for why this wraps the
        # terminal-event construction too, not just ingestion. Known usage received before
        # the failure (exact input, provider's cumulative output count) is preserved.
        try:
            return tracker.interrupt()  # uses the monotonic values recorded via observe_usage
        except Exception:  # noqa: BLE001 — even malformed captured values must not escape
            return tracker.interrupt()
