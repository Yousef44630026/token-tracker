"""Propagation async/thread-safe du contexte trace/span. (Phase 1)

Step 1 of Phase 1: the immutable *identity model*.

A ``TraceContext`` is the ambient identity that every token event attaches to. It
is intentionally a frozen dataclass — propagation works by *rebinding* the active
context (set/reset), never by mutating a shared object, which is what keeps parallel
async calls from cross-contaminating each other (added in a later step).

Identity rules (see CLAUDE.md INV-5):
  - ``trace_id``                root of one logical run; preserved across all spans.
  - ``span_id``                 one unit of work (an LLM call, a tool call, a stream).
  - ``parent_span_id``          the span this one was opened under (None at the root).
  - ``request_correlation_id``  one *attempt* of a provider call. A span may contain
                                retries = multiple calls, so supersession correlates
                                on THIS, never on span_id.
  - business_id / workflow / environment  cross-cutting labels, inherited by children.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from tracker.context.model import TraceContext, new_trace

# ---------------------------------------------------------------------------
# Propagation core — ambient active context (step 3)
# ---------------------------------------------------------------------------
#
# The active context lives in a ContextVar, NOT a thread-local: contextvars are
# per-task and are copied into asyncio tasks at creation, so parallel async calls
# each see the right parent and cannot clobber one another. We always rebind via
# set()/reset(token) (the @contextmanager guarantees the reset, even on exceptions),
# and TraceContext is frozen — there is no shared mutable state to race on.

_active: ContextVar[TraceContext | None] = ContextVar("tracker_active_context", default=None)
_active_flags: ContextVar[tuple[str, ...]] = ContextVar("tracker_active_context_flags", default=())

_HEADER_PREFIX = "X-TokenTracker-"


def current() -> TraceContext | None:
    """The context the current task/thread is executing inside, or None."""
    return _active.get()


def current_flags() -> tuple[str, ...]:
    """Data-quality flags inherited from the active propagation boundary."""
    return _active_flags.get()


@contextmanager
def _bind(ctx: TraceContext, *, flags: tuple[str, ...] | None = None) -> Iterator[TraceContext]:
    """Bind ``ctx`` as active for the duration; restore the previous on exit."""
    token = _active.set(ctx)
    flags_token = _active_flags.set(flags) if flags is not None else None
    try:
        yield ctx
    finally:
        if flags_token is not None:
            _active_flags.reset(flags_token)
        _active.reset(token)


@contextmanager
def trace(
    *,
    business_id: str | None = None,
    workflow: str | None = None,
    environment: str | None = None,
) -> Iterator[TraceContext]:
    """Open (and bind) a fresh root trace."""
    with _bind(
        new_trace(business_id=business_id, workflow=workflow, environment=environment),
        flags=(),
    ) as ctx:
        yield ctx


@contextmanager
def span() -> Iterator[TraceContext]:
    """Open (and bind) a child span of the active context.

    With no active context the span becomes its own root (a local span without an
    enclosing trace) — this is not a propagation failure, just a top-level unit.
    """
    parent = current()
    ctx = parent.child_span() if parent is not None else new_trace()
    with _bind(ctx) as bound:
        yield bound


@contextmanager
def retry() -> Iterator[TraceContext]:
    """Open (and bind) a retry of the active span (same span_id, new correlation id)."""
    cur = current()
    ctx = cur.retry() if cur is not None else new_trace()
    with _bind(ctx) as bound:
        yield bound


@dataclass(frozen=True, slots=True)
class ResolvedContext:
    """Result of resuming context from cross-service headers."""

    context: TraceContext
    propagation_lost: bool
    flags: tuple[str, ...] = field(default_factory=tuple)


def _has_tracker_headers(headers: Mapping[str, str]) -> bool:
    prefix = _HEADER_PREFIX.lower()
    return any(k.lower().startswith(prefix) for k in headers)


@contextmanager
def continue_from_headers(headers: Mapping[str, str]) -> Iterator[ResolvedContext]:
    """Resume context from inbound headers and bind it as active.

    - Valid identity headers -> open a child span of the remote span (not lost).
    - Tracker headers present but unresolvable (partial/corrupt) -> start a fresh
      root and flag ``propagation_lost`` (a parent was implied but could not be
      resolved). We never silently re-root a broken propagation without the flag.
    - No tracker headers at all -> a clean fresh root, no flag.

    Import is local to avoid a context<->headers import cycle.
    """
    from tracker.context.headers import extract

    remote = extract(headers)
    if remote is not None:
        ctx = remote.child_span()
        lost = False
    else:
        ctx = new_trace()
        lost = _has_tracker_headers(headers)

    flags: tuple[str, ...] = ("propagation_lost",) if lost else ()
    with _bind(ctx, flags=flags):
        yield ResolvedContext(context=ctx, propagation_lost=lost, flags=flags)
