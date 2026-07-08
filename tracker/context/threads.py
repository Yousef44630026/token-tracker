"""Context propagation into thread pools (Phase 1 companion).

ContextVars flow into asyncio tasks automatically, but NOT into a raw
``ThreadPoolExecutor.submit()``: the worker thread starts with an empty context, so an LLM
call made inside the pool silently becomes its own root — no trace linkage and, worse, no
``propagation_lost`` flag (no headers were involved, so nothing could notice). This module
closes that gap with two explicit tools:

  - ``carry_context(fn)``            wrap ONE callable with the context active right now;
  - ``ContextPropagatingExecutor``   a drop-in ``ThreadPoolExecutor`` whose ``submit``
                                     captures the caller's context at submit time.

Both run the callable inside a COPY of the captured context (``contextvars.copy_context``),
so worker-side rebinds (opening spans, retries) stay isolated and can never leak back into
the submitter — the same no-shared-mutable-state property the rest of propagation relies on.
"""

from __future__ import annotations

import contextvars
import functools
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")


def carry_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap ``fn`` so it runs inside the context active at WRAP time.

    The context is captured once, when this function is called — not when the wrapped
    callable eventually runs — so the returned callable pins the submitter's trace/span
    identity even if it executes after the submitting scope has exited.
    """
    captured = contextvars.copy_context()

    @functools.wraps(fn)
    def bound(*args: Any, **kwargs: Any) -> T:
        # run() executes inside a copy owned by this call: worker-side set/reset are isolated.
        return captured.copy().run(fn, *args, **kwargs)

    return bound


class ContextPropagatingExecutor(ThreadPoolExecutor):
    """A ``ThreadPoolExecutor`` whose ``submit`` carries the caller's context to the worker.

    Drop-in replacement: each ``submit`` captures the submitting thread/task's context at
    submit time (via ``carry_context``), so every worker sees exactly its own submitter's
    trace/span — never another submitter's, never an empty root.
    """

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        return super().submit(carry_context(fn), *args, **kwargs)


__all__ = ["ContextPropagatingExecutor", "carry_context"]
