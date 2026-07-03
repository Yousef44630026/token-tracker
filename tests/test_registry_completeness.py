"""Extra — the adapter registry covers EVERY concrete adapter (anti-drift guard).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_registry_completeness.py

If a new adapter class exists but is not registered, `create_adapter` would wrongly report it
as unsupported. This walks every concrete BaseAPISurfaceAdapter subclass and asserts it is
reachable through the registry — exactly the drift that had occurred repeatedly (Mistral /
Cohere / Voyage / Vertex AI / Bedrock embeddings, and later OpenAI / Azure OpenAI embeddings).

IMPORTANT: `BaseAPISurfaceAdapter.__subclasses__()` only sees classes whose module has
actually been imported somewhere in this process. Relying on `import tracker.adapters.registry`
alone to trigger those imports is exactly what let the OpenAI/Azure embeddings adapters go
undetected: registry.py itself never imported them, so nothing defined those classes, so
`__subclasses__()` silently didn't see them and this guard reported false success. To make the
guard self-sufficient (independent of what registry.py or any other test happens to import),
we explicitly walk and import every module in the `tracker.adapters` package first.
"""

import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tracker.adapters as _adapters_pkg  # noqa: E402
from tracker.adapters import available_adapters, create_adapter  # noqa: E402
from tracker.adapters.base import BaseAPISurfaceAdapter  # noqa: E402


def _import_every_adapter_module() -> list[str]:
    """Force-import every module under tracker/adapters/, independent of registry.py."""
    imported = []
    for module_info in pkgutil.iter_modules(_adapters_pkg.__path__, _adapters_pkg.__name__ + "."):
        importlib.import_module(module_info.name)
        imported.append(module_info.name)
    return imported


_imported_modules = _import_every_adapter_module()

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def concrete_adapters():
    seen, stack, out = set(), list(BaseAPISurfaceAdapter.__subclasses__()), []
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        if getattr(cls, "provider", "") and getattr(cls, "api_surface", ""):
            out.append(cls)
    return out


check(len(_imported_modules) >= 15, f"force-imported every adapter module (got {len(_imported_modules)})")

pairs = available_adapters()

# every registered pair instantiates with matching identity attributes
for provider, surface in pairs:
    a = create_adapter(provider, surface)
    check(a.provider == provider and a.api_surface == surface, f"{provider}/{surface}: registry key matches adapter attrs")

# COMPLETENESS — no concrete adapter escapes the registry
registered_types = {type(create_adapter(p, s)) for p, s in pairs}
concrete = concrete_adapters()
check(len(concrete) >= 15, f"found the full set of concrete adapters (got {len(concrete)})")
for cls in concrete:
    check(cls in registered_types, f"{cls.__name__} ({cls.provider}/{cls.api_surface}) is registered")

# every adapter known to have drifted out of the registry at some point is now reachable
for provider, surface in [
    ("mistral", "chat_completions"),
    ("cohere", "chat"),
    ("voyage", "rerank"),
    ("vertex_ai", "generate_content"),
    ("bedrock", "embeddings"),
    ("openai", "embeddings"),
    ("azure_openai", "embeddings"),
]:
    check((provider, surface) in pairs, f"{provider}/{surface} now registered (was missing)")

# --- collision detection: two adapters claiming the SAME (provider, api_surface) must raise,
# never silently let one clobber the other in the discovered registry. Exercises the
# _discover_adapters() branch added alongside the auto-discovery rewrite, previously untested.
from tracker.adapters.registry import _discover_adapters  # noqa: E402


class _CollisionAdapterOne(BaseAPISurfaceAdapter):
    provider = "test_collision_provider"
    api_surface = "test_collision_surface"

    def extract_usage_from_response(self, response):  # pragma: no cover - never called
        raise NotImplementedError

    def extract_usage_from_stream_event(self, event):  # pragma: no cover - never called
        raise NotImplementedError


class _CollisionAdapterTwo(BaseAPISurfaceAdapter):
    provider = "test_collision_provider"
    api_surface = "test_collision_surface"

    def extract_usage_from_response(self, response):  # pragma: no cover - never called
        raise NotImplementedError

    def extract_usage_from_stream_event(self, event):  # pragma: no cover - never called
        raise NotImplementedError


try:
    _discover_adapters()
    collision_raised = False
except RuntimeError as exc:
    collision_raised = "test_collision_provider" in str(exc) and "test_collision_surface" in str(exc)

check(collision_raised, "two adapters claiming the same (provider, api_surface) raise a descriptive RuntimeError")

# the module-level registry built at import time is untouched by that (failed) rediscovery —
# real adapters still resolve normally afterwards
sane_adapter = create_adapter("openai", "chat_completions")
check(sane_adapter.provider == "openai", "registry still resolves real adapters after a rediscovery collision elsewhere")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
