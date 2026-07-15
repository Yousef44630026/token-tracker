"""Correlated supersession (INV-5). (Phase 3)

A partial stream estimate is matched to its final-usage event by ``request_correlation_id``
— NOT ``span_id``, because one span may contain retries (multiple logical calls). On a
match the partial is marked ``superseded=True``, ``superseded_by=final.event_id``, and the
``"superseded"`` data-quality flag is raised. A superseded event contributes 0 everywhere.

One ``request_correlation_id`` represents exactly ONE logical attempt: a genuine retry is
issued its own, new correlation id (see ``context/propagation.py``'s ``retry()``). So if MORE
THAN ONE final-usage event ever shares the same correlation id, that is not two independent
calls — it is a duplicate measurement of the same attempt (e.g. an at-least-once delivery
firing a completion event twice) and must not be double-counted. Exactly one is kept
authoritative (the latest by ``timestamp`` when available, else the first in input order,
which keeps this deterministic for a given input rather than accidental); every other
final-usage event in the group is treated the same as a partial and superseded by it.

That collapse is only *safe* while the upstream invariant holds. If it is ever violated — an id
collision, or a caller reusing a correlation id for a genuinely DIFFERENT call — superseding one
final silently drops a real call's tokens (an undercount) with no signal. So this layer does not
merely trust the invariant, it defends it: when two superseded-as-duplicate finals carry
DIFFERENT content hashes (request_hash / response_hash), they cannot be the same call delivered
twice, so we still supersede (to never overcount) but ALSO raise ``correlation_id_collision`` so
the dropped tokens are auditable rather than invisible. When the hashes match (a true redelivery)
or are absent (cannot prove a collision), no such flag is raised — no false alarms.

Supersession is set HERE (the reconciler / stream tracker), never by an adapter.
"""

from __future__ import annotations

import datetime as dt

from tracker.models.enums import DataQualityFlag, TokenType, UsageSource
from tracker.models.token_event import TokenEvent

SUPERSEDED_FLAG = DataQualityFlag.SUPERSEDED.value
COLLISION_FLAG = DataQualityFlag.CORRELATION_ID_COLLISION.value


def _looks_like_distinct_call(a: TokenEvent, b: TokenEvent) -> bool:
    """True if two finals sharing a correlation id appear to be genuinely DIFFERENT calls.

    Judged only by content hashes that are present on BOTH events: if either the request_hash
    or the response_hash is known on both and differs, they cannot be the same call delivered
    twice. Absent hashes make it unprovable, so this returns False (stay quiet — never a false
    alarm)."""
    for attr in ("response_hash", "request_hash"):
        av, bv = getattr(a, attr), getattr(b, attr)
        if av is not None and bv is not None and av != bv:
            return True
    return False


PARTIAL_STREAM_ESTIMATE_FLAG = DataQualityFlag.PARTIAL_STREAM_ESTIMATE.value


def _is_partial_estimate(event: TokenEvent) -> bool:
    """True if the event is a partial-stream estimate (vs a real/final usage event).

    Primary signal is the ``partial_stream_estimate`` flag — its single producer is the
    stream tracker, set exactly on interrupt events. The all-quantities-partial-source shape
    is kept as a fallback for events built outside the tracker. The flag matters because an
    enriched partial may legitimately carry an EXACT provider-sourced input (received before
    the stream died) alongside its estimated output — that provider-sourced quantity must not
    make the event look like final usage."""
    if PARTIAL_STREAM_ESTIMATE_FLAG in event.data_quality_flags:
        return True
    quantities = event.quantities
    if not quantities:
        return False
    return all(q.usage_source == UsageSource.PARTIAL_STREAM_TOKENIZER for q in quantities)


def _is_final_usage(event: TokenEvent) -> bool:
    """True if the event carries real provider usage (a supersession target).

    A partial estimate is never a final, even when it carries provider-sourced quantities
    (see ``_is_partial_estimate``) — otherwise an enriched partial could be picked as the
    authoritative final and supersede the REAL usage."""
    if _is_partial_estimate(event):
        return False
    if not event.is_authoritative:
        return False
    return any(
        q.quantity is not None and q.usage_source in (UsageSource.PROVIDER_RESPONSE, UsageSource.PROVIDER_STREAM_FINAL)
        for q in event.quantities
    )


def _provider_contributing_types(event: TokenEvent) -> set[TokenType]:
    """Provider-measured token types that would contribute on this event."""
    provider_sources = {UsageSource.PROVIDER_RESPONSE, UsageSource.PROVIDER_STREAM_FINAL}
    return {
        quantity.token_type
        for quantity in event.quantities
        if quantity.included_in_total and quantity.usage_source in provider_sources
    }


def _can_supersede_partial(final: TokenEvent, partial: TokenEvent) -> bool:
    """Return whether keeping both events could duplicate usage or retain a stale output.

    An input-only final remains additive with an output-only partial. An enriched partial,
    however, may already carry the same exact provider input as that final; keeping both
    would count one request's input twice, so the final conservatively supersedes the whole
    partial event.
    """
    output_types = {TokenType.OUTPUT, TokenType.AUDIO_OUTPUT, TokenType.RERANK_OUTPUT}
    has_measured_output = any(
        q.quantity is not None
        and q.token_type in output_types
        and q.usage_source in (UsageSource.PROVIDER_RESPONSE, UsageSource.PROVIDER_STREAM_FINAL)
        for q in final.quantities
    )
    if has_measured_output:
        return True
    return bool(_provider_contributing_types(final) & _provider_contributing_types(partial))


def _pick_authoritative_final(finals: list[TokenEvent]) -> TokenEvent:
    """Choose the one authoritative final among duplicates sharing a correlation id.

    Prefers the latest timestamp instant, normalizing ISO-8601 offsets to UTC. Falls back
    to the first in input order when timestamps are absent, invalid, or tied, so the choice
    is always deterministic for a given input.
    """
    timestamped = [(event, parsed) for event in finals if (parsed := _parse_timestamp_utc(event.timestamp)) is not None]
    if timestamped:
        return max(timestamped, key=lambda item: item[1])[0]
    return finals[0]


def _parse_timestamp_utc(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _clear_supersession(event: TokenEvent) -> None:
    """Reset reconciler-owned supersession state before recomputing it."""
    event.superseded = False
    event.superseded_by = None
    event.data_quality_flags = [flag for flag in event.data_quality_flags if flag not in {SUPERSEDED_FLAG, COLLISION_FLAG}]


def reconcile_supersession(events: list[TokenEvent]) -> list[TokenEvent]:
    """Mark partials AND duplicate finals as superseded by the one authoritative final
    sharing their correlation id.

    Mutates the events in place (and returns the same list for convenience). Idempotent:
    re-running over already-reconciled events makes no further change. If a correlation
    group has no final usage, its partials are left untouched (they remain the best estimate
    available — supersession is never invented).
    """
    for event in events:
        _clear_supersession(event)

    by_rcid: dict[str, list[TokenEvent]] = {}
    for event in events:
        by_rcid.setdefault(event.request_correlation_id, []).append(event)

    for group in by_rcid.values():
        if len(group) < 2:
            continue
        finals = [e for e in group if _is_final_usage(e)]
        if not finals:
            continue
        final = _pick_authoritative_final(finals)
        duplicate_final_ids = {e.event_id for e in finals if e is not final}
        for event in group:
            if event is final:
                continue
            is_duplicate_final = event.event_id in duplicate_final_ids
            if is_duplicate_final or (_is_partial_estimate(event) and _can_supersede_partial(final, event)):
                event.superseded = True
                event.superseded_by = final.event_id
                if SUPERSEDED_FLAG not in event.data_quality_flags:
                    event.data_quality_flags.append(SUPERSEDED_FLAG)
                # A duplicate FINAL whose content differs from the kept final is not a
                # redelivery of the same call — it is a correlation-id collision. Keep the
                # conservative supersede (never overcount) but make the dropped tokens visible.
                # (Partials legitimately differ from their final, so they are never flagged.)
                if is_duplicate_final and _looks_like_distinct_call(event, final):
                    if COLLISION_FLAG not in event.data_quality_flags:
                        event.data_quality_flags.append(COLLISION_FLAG)

    return events
