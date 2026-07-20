"""Consume provider streams without promoting partial usage to final usage.

Adapters extract usage-bearing events and explicitly identify terminal usage. The consumer
preserves exact provider counts already observed, estimates interrupted output, and carries
the same schema-drift evidence as the non-streaming normalizer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from tracker.context.propagation import TraceContext
from tracker.models.enums import DataQualityFlag, TokenType
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.normalization.usage_contract import inspect_usage_contract, usage_contract_observation
from tracker.streaming.stream_tracker import StreamTracker


def consume_stream(
    events: Iterable[Any],
    adapter: Any,
    *,
    context: TraceContext,
    text_extractor: Callable[[Any], str | None] | None = None,
    model: str | None = None,
) -> TokenEvent:
    """Consume a provider stream and return one exact or explicitly partial event."""
    tracker = StreamTracker.from_context(
        context,
        provider=adapter.provider,
        api_surface=adapter.api_surface,
        model=model,
    )
    provider_total: int | None = None
    final_model: str | None = model
    final_quantities: dict[tuple[TokenType, str | None, str | None], TokenQuantity] = {}
    saw_terminal_usage = False
    terminal_status: str | None = None
    stream_flags: list[str] = []
    unmapped_paths: set[str] = set()

    def add_flag(flag: str) -> None:
        if flag not in stream_flags:
            stream_flags.append(flag)

    def observation_extra() -> dict[str, object]:
        extra = usage_contract_observation(sorted(unmapped_paths))
        if terminal_status is not None:
            extra["status"] = terminal_status
        return extra

    def finish_exact() -> TokenEvent:
        return tracker.complete_with_quantities(
            quantities=list(final_quantities.values()),
            provider_total_tokens=provider_total,
            model=final_model,
            extra_flags=stream_flags,
            observation_extra=observation_extra(),
        )

    try:
        for event in events:
            if text_extractor is not None:
                delta = text_extractor(event)
                if delta:
                    tracker.feed(delta)

            usage = adapter.extract_usage_from_stream_event(event)
            if usage is None:
                continue

            usage_flags, usage_unmapped = inspect_usage_contract(adapter, usage)
            for flag in usage_flags:
                add_flag(flag)
            unmapped_paths.update(usage_unmapped)
            if usage.model is not None:
                final_model = usage.model

            has_output_quantity = False
            for quantity in usage.quantities:
                if quantity.quantity is None:
                    continue
                key = (quantity.token_type, quantity.token_role, quantity.subtotal_of)
                final_quantities[key] = quantity
                if quantity.token_type == TokenType.INPUT:
                    tracker.observe_usage(input_tokens=quantity.quantity)
                elif quantity.token_type == TokenType.OUTPUT:
                    has_output_quantity = True
                    tracker.observe_usage(output_tokens=quantity.quantity)

            # Built-ins set stream_terminal explicitly. Custom adapters retain the legacy
            # behavior where a usage-bearing output event is assumed to be terminal.
            terminal = getattr(usage, "stream_terminal", None)
            if terminal is None:
                terminal = has_output_quantity
            saw_terminal_usage = saw_terminal_usage or terminal
            usage_status = getattr(usage, "stream_status", None)
            if terminal and usage_status is not None:
                terminal_status = usage_status
            if usage.provider_total_tokens is not None:
                provider_total = usage.provider_total_tokens

        if saw_terminal_usage and final_quantities:
            return finish_exact()

        add_flag(DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value)
        return tracker.interrupt(
            extra_flags=stream_flags,
            observation_extra=observation_extra(),
        )
    except Exception:  # tracking must never break the provider stream consumer
        # A transport may raise after a genuine final usage event. Preserve that exact
        # terminal measurement if it still validates; otherwise fail closed to a partial.
        if saw_terminal_usage and final_quantities:
            try:
                return finish_exact()
            except Exception:  # malformed terminal usage
                add_flag(DataQualityFlag.NORMALIZATION_ERROR.value)
        else:
            add_flag(DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value)
        try:
            return tracker.interrupt(
                extra_flags=stream_flags,
                observation_extra=observation_extra(),
            )
        except Exception:
            return tracker.interrupt(extra_flags=stream_flags)
