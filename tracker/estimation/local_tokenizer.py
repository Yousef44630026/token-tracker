"""Local token estimator. (Phase 7)

A coarse, dependency-free token estimate used ONLY for partial-stream output when the
provider's exact usage is not available. Any quantity produced from this is precision
``estimate`` / source ``partial_stream_tokenizer`` (or ``local_tokenizer``) — never ``exact``.

The heuristic (~4 characters per token) is intentionally simple: it exists to surface an
order-of-magnitude estimate for an interrupted stream, not to compete with a real tokenizer.
"""

from __future__ import annotations

_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` (0 for empty, else ~len/4, at least 1)."""
    if not text:
        return 0
    return max(1, round(len(text) / _CHARS_PER_TOKEN))
