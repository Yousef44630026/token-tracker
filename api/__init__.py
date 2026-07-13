"""Standard-library HTTP collector."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.main import create_server, make_http_transport

__all__ = ["create_server", "make_http_transport"]


def __getattr__(name: str) -> Any:
    """Load public helpers without pre-importing ``api.main`` for ``python -m``."""
    if name in __all__:
        from api import main

        return getattr(main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
