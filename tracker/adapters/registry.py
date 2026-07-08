"""Built-in adapter lookup by provider and API surface — auto-discovered.

Every concrete ``BaseAPISurfaceAdapter`` subclass under ``tracker/adapters/`` is picked up
automatically: we force-import every module in the package (``pkgutil``), then walk the
subclass tree and index each concrete class (one with non-empty ``provider``/``api_surface``)
by that pair. There is no manually maintained list to fall out of sync.

This registry broke twice from the old hand-maintained-dict design (Mistral/Cohere/Voyage/
VertexAI/Bedrock-embeddings, then OpenAI/Azure-OpenAI embeddings): an adapter class existed
but nobody added its dict entry, so ``create_adapter`` wrongly reported it as unsupported.
Auto-discovery makes that specific class of bug structurally impossible — a new adapter is
registered the moment its module exists, with no separate step to remember.
"""

from __future__ import annotations

import importlib
import pkgutil

import tracker.adapters as _adapters_pkg
from tracker.adapters.base import BaseAPISurfaceAdapter
from tracker.adapters.generic_fallback_adapter import GenericFallbackAdapter

_PROVIDER_ALIASES = {
    "azureopenai": "azure_openai",
    "google": "gemini",
}


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _import_every_adapter_module() -> None:
    """Force-import every module under tracker/adapters/ so all subclasses are defined."""
    for module_info in pkgutil.iter_modules(_adapters_pkg.__path__, _adapters_pkg.__name__ + "."):
        importlib.import_module(module_info.name)


def _discover_adapters() -> dict[tuple[str, str], type[BaseAPISurfaceAdapter]]:
    """Walk every concrete BaseAPISurfaceAdapter subclass, indexed by (provider, api_surface)."""
    _import_every_adapter_module()
    registry: dict[tuple[str, str], type[BaseAPISurfaceAdapter]] = {}
    seen: set[type] = set()
    stack = list(BaseAPISurfaceAdapter.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        provider, surface = getattr(cls, "provider", ""), getattr(cls, "api_surface", "")
        if not provider or not surface:
            continue  # an intermediate/abstract helper, not a concrete adapter
        key = (provider, surface)
        existing = registry.get(key)
        if existing is not None and existing is not cls:
            raise RuntimeError(
                f"adapter registry collision: {existing.__name__} and {cls.__name__} " f"both claim ({provider!r}, {surface!r})"
            )
        registry[key] = cls
    return registry


_ADAPTERS: dict[tuple[str, str], type[BaseAPISurfaceAdapter]] = _discover_adapters()


def create_adapter(provider: str, api_surface: str) -> BaseAPISurfaceAdapter:
    """Instantiate a built-in adapter, raising a descriptive error if unsupported."""
    normalized_provider = _normalize(provider)
    normalized_provider = _PROVIDER_ALIASES.get(normalized_provider, normalized_provider)
    key = normalized_provider, _normalize(api_surface)
    try:
        adapter_type = _ADAPTERS[key]
    except KeyError as exc:
        supported = ", ".join(f"{p}/{s}" for p, s in sorted(_ADAPTERS))
        raise ValueError(f"unsupported adapter {provider!r}/{api_surface!r}; supported: {supported}") from exc
    return adapter_type()


def create_adapter_with_fallback(provider: str, api_surface: str) -> BaseAPISurfaceAdapter:
    """Resolve the dedicated adapter, or a ``GenericFallbackAdapter`` when none exists.

    The explicit opt-in for capture paths that must never drop an observed call: an unknown
    provider is captured open (its real usage, stamped with its real provider/surface) and
    counted closed (everything ``unverified`` via the central table's fail-closed default,
    contributing 0 until a dedicated adapter encodes the provider's additivity truth).
    ``create_adapter`` itself stays strict so misconfigurations still fail loudly.
    """
    normalized_provider = _normalize(provider)
    normalized_provider = _PROVIDER_ALIASES.get(normalized_provider, normalized_provider)
    normalized_surface = _normalize(api_surface)
    adapter_type = _ADAPTERS.get((normalized_provider, normalized_surface))
    if adapter_type is not None:
        return adapter_type()
    return GenericFallbackAdapter(normalized_provider, normalized_surface)


def available_adapters() -> tuple[tuple[str, str], ...]:
    """Return stable provider/surface pairs for discovery and validation."""
    return tuple(sorted(_ADAPTERS))


__all__ = ["available_adapters", "create_adapter", "create_adapter_with_fallback"]
