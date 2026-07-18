"""Best-available local token estimator. (Phase 7)

A coarse, dependency-free token estimate used ONLY for partial-stream output when the
provider's exact usage is not available. Any quantity produced from this is precision
``estimate`` / source ``partial_stream_tokenizer`` (or ``local_tokenizer``) — never ``exact``.

``tiktoken`` with ``cl100k_base`` is a required runtime dependency. A dependency-free
four-characters-per-token heuristic remains only as an emergency capture guard; callers
persist its backend name and the Doctor treats its activation as a readiness failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

_CHARS_PER_TOKEN = 4
_TIKTOKEN_BACKEND = "tokentap_cl100k_base"
_FALLBACK_BACKEND = "tracker_char4_fallback"


@dataclass(frozen=True)
class TokenEstimate:
    quantity: int
    estimator: str
    text_characters: int


def estimate_tokens_char4(text: str) -> int:
    """Dependency-free fallback used when no tokenizer backend is available."""
    if not text:
        return 0
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


@lru_cache(maxsize=1)
def _best_counter() -> tuple[Callable[[str], int], str]:
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - estimation must never block event capture
        return estimate_tokens_char4, _FALLBACK_BACKEND
    return lambda text: len(encoding.encode(text, disallowed_special=())), _TIKTOKEN_BACKEND


def estimate_with_metadata(text: str) -> TokenEstimate:
    """Estimate text and disclose the exact backend used."""
    counter, backend = _best_counter()
    return TokenEstimate(quantity=counter(text), estimator=backend, text_characters=len(text))


def estimator_backend() -> str:
    """Return the backend currently selected for local estimates."""
    return _best_counter()[1]


def tokenizer_status() -> dict[str, object]:
    """Operational status suitable for doctor/readiness output."""
    backend = estimator_backend()
    return {
        "backend": backend,
        "tokenizer_available": backend == _TIKTOKEN_BACKEND,
        "fallback_characters_per_token": _CHARS_PER_TOKEN if backend == _FALLBACK_BACKEND else None,
    }


def estimate_tokens(text: str) -> int:
    """Estimate tokens with the best available backend."""
    return estimate_with_metadata(text).quantity
