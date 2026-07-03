"""Derived fields (INV-2): the canonical computed-only layer. (Phase 3)

These are NEVER stored and NEVER serialized into JSONL. The models already expose the same
values as ``@property`` (so a freshly read-back object always re-derives correctly); these
free functions are the canonical entry points the export / analytics / rollup layers call,
keeping every derivation in one named place. They intentionally delegate to the model
properties so the rule can never fork into two implementations.
"""

from __future__ import annotations

from tracker.models.token_event import TokenEvent
from tracker.models.token_quantity import TokenQuantity


def included_in_total(q: TokenQuantity) -> bool:
    """total_contributing AND a known quantity (INV-2 / INV-4 / INV-6)."""
    return q.included_in_total


def quantity_in_total(q: TokenQuantity) -> int:
    """The quantity if it is included, else 0. Only this column is ever summed."""
    return q.quantity_in_total


def export_warning(q: TokenQuantity) -> str | None:
    """Why a quantity is excluded from the total, or None."""
    return q.export_warning


def event_contributing_tokens(event: TokenEvent) -> int:
    """0 if the event is superseded (INV-5), else sum(quantity_in_total)."""
    return event.event_contributing_tokens


def event_total_mismatch(event: TokenEvent) -> int | None:
    """provider_total_tokens - sum(quantity_in_total), or None if there is no provider total."""
    return event.event_total_mismatch
