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
from tracker.streaming.status import merge_stream_status
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
    partial_quantities: dict[tuple[TokenType, str | None, str | None], TokenQuantity] = {}
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

            terminal = getattr(usage, "stream_terminal", None)
            has_output_quantity = any(
                quantity.quantity is not None and quantity.token_type == TokenType.OUTPUT
                for quantity in usage.quantities
            )
            # Custom adapters retain the legacy terminal convention. Built-ins are explicit.
            if terminal is None:
                terminal = has_output_quantity
            target_quantities = final_quantities if terminal else partial_quantities
            for quantity in usage.quantities:
                if quantity.quantity is None:
                    continue
                key = (quantity.token_type, quantity.token_role, quantity.subtotal_of)
                target_quantities[key] = quantity
                if quantity.token_type == TokenType.INPUT:
                    tracker.observe_usage(input_tokens=quantity.quantity)
                elif quantity.token_type == TokenType.OUTPUT:
                    tracker.observe_usage(output_tokens=quantity.quantity)

            if terminal and usage.quantities:
                # Anthropic splits immutable input into message_start and terminal output into
                # message_delta. Merge those two snapshots only when the terminal itself carries
                # usage. A terminal marker without usage can never promote partial counters.
                final_quantities = {**partial_quantities, **final_quantities}
                saw_terminal_usage = True
            elif terminal:
                add_flag(DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value)
            usage_status = getattr(usage, "stream_status", None)
            if usage_status is not None and (terminal or usage_status in {"incomplete", "failed"}):
                terminal_status = merge_stream_status(terminal_status, usage_status)
            if terminal and usage.provider_total_tokens is not None:
                provider_total = usage.provider_total_tokens

        if saw_terminal_usage and final_quantities:
            return finish_exact()

        add_flag(DataQualityFlag.PROVIDER_STREAM_USAGE_MISSING.value)
        terminal_status = merge_stream_status(terminal_status, "incomplete")
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
        terminal_status = merge_stream_status(terminal_status, "incomplete")
        try:
            return tracker.interrupt(
                extra_flags=stream_flags,
                observation_extra=observation_extra(),
            )
        except Exception:
            return tracker.interrupt(extra_flags=stream_flags)
