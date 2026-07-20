"""Streaming tracker (INV-3 / INV-5 / INV-6). (Phase 7)

Tracks one streamed call and emits a TokenEvent for its terminal state. The output token
stays ``token_type="output"`` throughout — only precision/source/flags change (INV-3):

  - complete(...)                -> output EXACT from the provider's final usage;
  - interrupt()                  -> output ESTIMATE from the local tokenizer over the text
                                    seen so far, flags partial_stream_estimate +
                                    stream_interrupted;
  - resolve_with_final_usage(...)-> the real usage that arrives after an interrupt; the
                                    previously emitted partial is superseded by
                                    request_correlation_id (INV-5), so it contributes 0;
  - timeout()                    -> output quantity None / UNKNOWN with reason stream_timeout
                                    (INV-6: a lost count is surfaced, never a confident zero).

The tracker is one of the two layers (with the reconciler) allowed to set supersession.
Additivity is taken from the central table (INV-4); input/output are total_contributing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from tracker.context.propagation import TraceContext, current, current_flags
from tracker.estimation.local_tokenizer import estimate_tokens, estimator_backend
from tracker.models.enums import (
    DataQualityFlag,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)
from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity
from tracker.normalization.additivity import assign_additivity
from tracker.normalization.event_builder import build_event
from tracker.normalization.supersession import reconcile_supersession
from tracker.observability.observation import Observation

PARTIAL_STREAM_ESTIMATE_FLAG = DataQualityFlag.PARTIAL_STREAM_ESTIMATE.value
STREAM_INTERRUPTED_FLAG = DataQualityFlag.STREAM_INTERRUPTED.value


class StreamTracker:
    """Accumulates a streamed response and emits the terminal TokenEvent."""

    @classmethod
    def from_context(
        cls,
        context: TraceContext,
        *,
        provider: str | None = None,
        api_surface: str | None = None,
        model: str | None = None,
        estimator: Callable[[str], int] = estimate_tokens,
        estimator_name: str | None = None,
    ) -> StreamTracker:
        """Create a tracker without manually copying propagated identity fields."""
        return cls(
            request_correlation_id=context.request_correlation_id,
            trace_id=context.trace_id,
            span_id=context.span_id,
            parent_span_id=context.parent_span_id,
            provider=provider,
            api_surface=api_surface,
            model=model,
            business_id=context.business_id,
            workflow=context.workflow,
            environment=context.environment,
            estimator=estimator,
            estimator_name=estimator_name,
            context_flags=current_flags() if current() is context else (),
        )

    def __init__(
        self,
        *,
        request_correlation_id: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None = None,
        provider: str | None = None,
        api_surface: str | None = None,
        model: str | None = None,
        business_id: str | None = None,
        workflow: str | None = None,
        environment: str | None = None,
        estimator: Callable[[str], int] = estimate_tokens,
        estimator_name: str | None = None,
        context_flags: Iterable[str] = (),
    ) -> None:
        self.request_correlation_id = request_correlation_id
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.provider = provider
        self.api_surface = api_surface
        self.model = model
        self.business_id = business_id
        self.workflow = workflow
        self.environment = environment
        self._context_flags = tuple(context_flags)
        self._estimator = estimator
        self._estimator_name = estimator_name or (
            estimator_backend() if estimator is estimate_tokens else getattr(estimator, "__name__", "injected_estimator")
        )
        self._chunks: list[str] = []
        self._partial: TokenEvent | None = None
        # Latest CUMULATIVE usage the provider reported mid-stream (a floor if interrupted).
        self._observed_input: int | None = None
        self._observed_output: int | None = None

    # --- ingest -----------------------------------------------------------------------
    def feed(self, text_delta: str) -> None:
        """Accumulate one streamed text delta."""
        if text_delta:
            self._chunks.append(text_delta)

    def observe_usage(self, *, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        """Record cumulative provider usage seen in a MID-stream event.

        Providers that stream running totals (e.g. Anthropic ``message_delta.usage``) let an
        interrupted stream fall back on the provider's OWN last count — a near-exact floor —
        instead of a tokenizer guess. Counts are cumulative, so we keep the maximum seen: a
        stale or duplicated event can never lower the floor.
        """
        if input_tokens is not None and (self._observed_input is None or input_tokens > self._observed_input):
            self._observed_input = input_tokens
        if output_tokens is not None and (self._observed_output is None or output_tokens > self._observed_output):
            self._observed_output = output_tokens

    @property
    def accumulated_text(self) -> str:
        return "".join(self._chunks)

    # --- helpers ----------------------------------------------------------------------
    def _new_event(
        self,
        quantities,
        provider_total,
        flags,
        *,
        model=None,
        observation_extra: Mapping[str, object] | None = None,
    ) -> TokenEvent:
        context = TraceContext(
            trace_id=self.trace_id,
            span_id=self.span_id,
            request_correlation_id=self.request_correlation_id,
            parent_span_id=self.parent_span_id,
            business_id=self.business_id,
            workflow=self.workflow,
            environment=self.environment,
        )
        observation = Observation(
            authoritative=True,
            status="incomplete" if STREAM_INTERRUPTED_FLAG in flags else "complete",
        )
        if observation_extra:
            observation.update(observation_extra)
        return build_event(
            context=context,
            provider=self.provider,
            api_surface=self.api_surface,
            model=model if model is not None else self.model,
            quantities=quantities,
            provider_total_tokens=provider_total,
            leading_flags=[*self._context_flags, *flags],
            observation=observation,
        )

    def _quantity(self, token_type, quantity, precision, source, unknown_reason=None, metadata=None):
        additivity, subtotal_of = assign_additivity(self.provider or "", self.api_surface or "", token_type)
        return TokenQuantity(
            token_type=token_type,
            quantity=quantity,
            precision_level=precision,
            usage_source=source,
            additivity=additivity,
            subtotal_of=subtotal_of,
            unknown_reason=unknown_reason,
            metadata=metadata or {},
        )

    def _usage_quantities(self, output_tokens, input_tokens, source, precision):
        quantities = [self._quantity(TokenType.OUTPUT, output_tokens, precision, source)]
        if input_tokens is not None:
            quantities.insert(
                0,
                self._quantity(TokenType.INPUT, input_tokens, PrecisionLevel.EXACT, source),
            )
        return quantities

    # --- terminal states --------------------------------------------------------------
    def complete(
        self,
        *,
        output_tokens: int,
        input_tokens: int | None = None,
        provider_total_tokens: int | None = None,
    ) -> TokenEvent:
        """Clean completion: emit EXACT usage from the provider's final stream event.

        An input the provider already reported mid-stream is carried forward when the final
        frame does not restate it (Anthropic sends the exact input once, in message_start, and
        its final usage frame carries OUTPUT only). Explicit ``input_tokens`` wins; otherwise
        the tracker falls back on ``observe_usage`` — the same "never throw away usage already
        received" rule ``interrupt()`` (S1) and ``timeout()`` (S2) follow. Dropping it here is
        silent, because a partial holding that input is superseded by this event (INV-5) and
        contributes 0, so the tokens would vanish from the pair entirely (S3 regression).

        Input is safe to carry forward — it does not grow during a stream, so a mid-stream input
        count IS the request's final input. The OUTPUT has no such fallback on purpose: a
        cumulative mid-stream output count is only ever a FLOOR for an estimate (see
        ``interrupt()``) and must never be promoted into an EXACT final.
        """
        if input_tokens is None:
            input_tokens = self._observed_input
        quantities = self._usage_quantities(
            output_tokens,
            input_tokens,
            UsageSource.PROVIDER_STREAM_FINAL,
            PrecisionLevel.EXACT,
        )
        return self._new_event(quantities, provider_total_tokens, [])

    def complete_with_quantities(
        self,
        *,
        quantities: list[TokenQuantity],
        provider_total_tokens: int | None = None,
        model: str | None = None,
        extra_flags: Iterable[str] = (),
        observation_extra: Mapping[str, object] | None = None,
    ) -> TokenEvent:
        """Clean completion preserving adapter-provided final stream quantities."""
        return self._new_event(
            quantities,
            provider_total_tokens,
            list(extra_flags),
            model=model,
            observation_extra=observation_extra,
        )

    def interrupt(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens_seen: int | None = None,
        extra_flags: Iterable[str] = (),
        observation_extra: Mapping[str, object] | None = None,
    ) -> TokenEvent:
        """Stream cut off without final usage: emit the best partial measurement available.

        Usage already RECEIVED before the cut is kept, never thrown away. Explicit arguments
        win; otherwise the tracker falls back on the cumulative usage it observed mid-stream
        (``observe_usage``):
          - ``input_tokens``: an exact input count the provider already sent (e.g. Anthropic
            message_start) — recorded EXACT/provider-sourced alongside the estimate;
          - ``output_tokens_seen``: the provider's cumulative output count from a mid-stream
            event — used as the output ESTIMATE when it beats the text-based one (the
            provider's own partial count can only be a floor of the final), and labeled
            PROVIDER_STREAM_PARTIAL so it is never mistaken for a complete response.
        The event stays a partial (flags partial_stream_estimate + stream_interrupted) and is
        superseded as usual if the real final usage later arrives (INV-5)."""
        if input_tokens is None:
            input_tokens = self._observed_input
        if output_tokens_seen is None:
            output_tokens_seen = self._observed_output
        estimated = self._estimator(self.accumulated_text)
        estimate_metadata = {
            "estimator": self._estimator_name,
            "text_characters": len(self.accumulated_text),
            "text_estimate_tokens": estimated,
        }
        if output_tokens_seen is not None and output_tokens_seen > estimated:
            output_q = self._quantity(
                TokenType.OUTPUT,
                output_tokens_seen,
                PrecisionLevel.ESTIMATE,
                UsageSource.PROVIDER_STREAM_PARTIAL,
                metadata={**estimate_metadata, "provider_partial_floor_tokens": output_tokens_seen},
            )
        else:
            output_q = self._quantity(
                TokenType.OUTPUT,
                estimated,
                PrecisionLevel.ESTIMATE,
                UsageSource.PARTIAL_STREAM_TOKENIZER,
                metadata=estimate_metadata,
            )
        quantities = [output_q]
        if input_tokens is not None:
            quantities.insert(
                0,
                self._quantity(
                    TokenType.INPUT,
                    input_tokens,
                    PrecisionLevel.EXACT,
                    UsageSource.PROVIDER_RESPONSE,
                ),
            )
        self._partial = self._new_event(
            quantities,
            None,
            [PARTIAL_STREAM_ESTIMATE_FLAG, STREAM_INTERRUPTED_FLAG, *extra_flags],
            observation_extra=observation_extra,
        )
        return self._partial

    def resolve_with_final_usage(
        self,
        *,
        output_tokens: int,
        input_tokens: int | None = None,
        provider_total_tokens: int | None = None,
    ) -> TokenEvent:
        """Real usage arriving after an interrupt: emit it and supersede the partial (INV-5)."""
        final = self.complete(
            output_tokens=output_tokens,
            input_tokens=input_tokens,
            provider_total_tokens=provider_total_tokens,
        )
        if self._partial is not None:
            reconcile_supersession([self._partial, final])
        return final

    def timeout(self, *, input_tokens: int | None = None) -> TokenEvent:
        """No OUTPUT arrived in time: emit output None / UNKNOWN, surfaced not zeroed (INV-6).

        The output is genuinely lost, so it stays a surfaced unknown (never a confident zero).
        But an EXACT input already received from the provider (e.g. Anthropic's message_start)
        is real, billed data and must not be thrown away just because the output timed out —
        the same rule ``interrupt()`` follows (S1 regression). Explicit ``input_tokens`` wins;
        otherwise the tracker falls back on the cumulative input it observed mid-stream.
        """
        if input_tokens is None:
            input_tokens = self._observed_input
        quantities = [
            self._quantity(
                TokenType.OUTPUT,
                None,
                PrecisionLevel.UNKNOWN,
                UsageSource.NONE,
                unknown_reason=UnknownReason.STREAM_TIMEOUT,
            )
        ]
        if input_tokens is not None:
            quantities.insert(
                0,
                self._quantity(
                    TokenType.INPUT,
                    input_tokens,
                    PrecisionLevel.EXACT,
                    UsageSource.PROVIDER_RESPONSE,
                ),
            )
        return self._new_event(quantities, None, [STREAM_INTERRUPTED_FLAG])
