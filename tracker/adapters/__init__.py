"""Provider API-surface adapters."""

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage


def create_adapter(provider: str, api_surface: str) -> BaseAPISurfaceAdapter:
    from tracker.adapters.registry import create_adapter as _create_adapter

    return _create_adapter(provider, api_surface)


def create_adapter_with_fallback(provider: str, api_surface: str) -> BaseAPISurfaceAdapter:
    from tracker.adapters.registry import create_adapter_with_fallback as _with_fallback

    return _with_fallback(provider, api_surface)


def available_adapters() -> tuple[tuple[str, str], ...]:
    from tracker.adapters.registry import available_adapters as _available_adapters

    return _available_adapters()


__all__ = [
    "BaseAPISurfaceAdapter",
    "NormalizedUsage",
    "available_adapters",
    "create_adapter",
    "create_adapter_with_fallback",
]
