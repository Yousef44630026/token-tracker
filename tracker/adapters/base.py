"""Adapter contract: BaseAPISurfaceAdapter + NormalizedUsage. (Phase 4)

An adapter is the per-provider, per-API-surface translator from a raw provider payload into
the model's source-of-truth facts. Its job and its limits (binding):

  - It ASSIGNS precision_level, additivity, and subtotal_of for each quantity, and extracts
    the raw provider_total_tokens. additivity comes from the centralized Phase 3 table
    (INV-4) so it is never inferred from the token_type string.
  - It MUST NOT compute any derived field (included_in_total / quantity_in_total /
    event_contributing_tokens / totals) — those live in derive/ (INV-2).
  - It MUST NOT set supersession — that is the reconciler / stream tracker's job (INV-5).

Concrete adapters (Phases 5/10) capture RECORDED REAL payloads as fixtures and implement
the abstract methods. This module only defines the shape they must satisfy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tracker.estimation.local_tokenizer import estimate_tokens
from tracker.models.enums import Additivity, Overlap, PrecisionLevel, TokenType, Trust, UsageSource
from tracker.models.token_quantity import TokenQuantity
from tracker.normalization.additivity import assign_additivity


def field_value(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an SDK object or a decoded mapping."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def usage_snapshot(value: Any) -> dict[str, Any] | None:
    """Return a bounded plain mapping for SDK-object schema inspection.

    Provider SDKs commonly expose usage as Pydantic/proto-style objects instead of dicts.
    The snapshot is ephemeral: only unfamiliar field paths are later retained, never raw
    values. Depth and collection caps keep malformed provider objects from amplifying memory.
    """
    seen: set[int] = set()

    def plain(current: Any, depth: int) -> Any:
        if current is None or isinstance(current, (str, int, float, bool)):
            return current
        if depth >= 8:
            return None
        identity = id(current)
        if identity in seen:
            return None
        seen.add(identity)
        try:
            if isinstance(current, Mapping):
                items = sorted(current.items(), key=lambda item: str(item[0]))[:128]
                return {str(key): plain(child, depth + 1) for key, child in items}
            if isinstance(current, (list, tuple)):
                return [plain(child, depth + 1) for child in current[:32]]
            for method_name in ("model_dump", "to_dict", "as_dict"):
                method = getattr(current, method_name, None)
                if not callable(method):
                    continue
                try:
                    converted = method()
                except Exception:  # provider SDK conversion helpers are best effort
                    continue
                if converted is not current:
                    return plain(converted, depth + 1)
            attributes = getattr(current, "__dict__", None)
            if isinstance(attributes, Mapping):
                public = {
                    str(key): child
                    for key, child in attributes.items()
                    if not str(key).startswith("_") and not callable(child)
                }
                return plain(public, depth + 1)
            return current
        finally:
            seen.discard(identity)

    snapshot = plain(value, 0)
    return snapshot if isinstance(snapshot, dict) else None


@dataclass
class NormalizedUsage:
    """The adapter's output: assigned source-of-truth facts for one provider call.

    Carries ASSIGNED facts only (quantities with precision/additivity/subtotal_of set, the
    raw provider total, and adapter-level data-quality flags such as ``raw_usage_missing`` or
    ``normalization_error``). It deliberately holds NO derived field and NO supersession —
    those are added downstream by derive/ and the reconciler, never by an adapter.
    """

    provider: str
    api_surface: str
    model: str | None = None
    quantities: list[TokenQuantity] = field(default_factory=list)
    provider_total_tokens: int | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    raw_usage: dict[str, Any] | None = None
    # None keeps third-party/custom adapters backward compatible. Built-in streaming
    # adapters set this explicitly so a cumulative mid-stream counter cannot be promoted
    # into an authoritative final measurement.
    stream_terminal: bool | None = None
    # Terminal provider status carried by lifecycle stream events. This remains operational
    # metadata (complete/incomplete/failed), orthogonal to whether the usage count is exact.
    stream_status: str | None = None


class BaseAPISurfaceAdapter(ABC):
    """Contract every provider/surface adapter implements.

    Subclasses set ``provider`` / ``api_surface`` and implement response + stream extraction.
    Common local estimation, total passthrough, error classification, field access, and
    quantity construction live here so provider modules contain provider-specific logic.
    """

    provider: str = ""
    api_surface: str = ""
    # Leaf paths in the provider's usage object that contain token counts and are either
    # mapped or deliberately ignored. List items use ``[]`` so cardinality stays bounded.
    recognized_usage_token_paths: frozenset[str] = frozenset()

    @staticmethod
    def _usage_leaf_paths(value: Any, prefix: str = "") -> list[str]:
        """Return normalized leaf paths for a decoded usage mapping."""
        if isinstance(value, dict):
            paths: list[str] = []
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                paths.extend(BaseAPISurfaceAdapter._usage_leaf_paths(child, path))
            return paths
        if isinstance(value, list):
            paths = []
            list_prefix = f"{prefix}[]"
            for child in value:
                paths.extend(BaseAPISurfaceAdapter._usage_leaf_paths(child, list_prefix))
            return paths
        return [prefix] if prefix else []

    def unmapped_usage_token_paths(self, raw_usage: dict[str, Any] | None) -> tuple[str, ...]:
        """Return up to eight unfamiliar token-looking paths for audit.

        This is detection only. Unknown values never become quantities automatically.
        Adapters without an explicit contract opt out rather than producing noisy guesses.
        """
        if not raw_usage or not self.recognized_usage_token_paths:
            return ()
        unknown = {
            path
            for path in self._usage_leaf_paths(raw_usage)
            if "token" in path.lower() and path not in self.recognized_usage_token_paths
        }
        return tuple(sorted(unknown)[:8])

    # --- provided: one shared INV-4 source of truth -----------------------------------
    def assign_additivity(self, token_type: TokenType) -> tuple[Additivity, str | None]:
        """Return ``(additivity, subtotal_of)`` for a token_type via the central table."""
        return assign_additivity(self.provider, self.api_surface, token_type)

    def build_quantity(
        self,
        token_type: TokenType,
        quantity: int | None,
        precision_level: PrecisionLevel,
        usage_source: UsageSource,
        unknown_reason=None,
        token_role: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TokenQuantity:
        """Build a TokenQuantity with additivity/subtotal_of already assigned (INV-4)."""
        additivity, subtotal_of = self.assign_additivity(token_type)
        return TokenQuantity(
            token_type=token_type,
            quantity=quantity,
            precision_level=precision_level,
            usage_source=usage_source,
            additivity=additivity,
            subtotal_of=subtotal_of,
            unknown_reason=unknown_reason,
            token_role=token_role,
            metadata=metadata or {},
        )

    def build_unverified_quantity(
        self,
        token_type: TokenType,
        quantity: int | None,
        precision_level: PrecisionLevel,
        usage_source: UsageSource,
        *,
        overlap: Overlap = Overlap.INDEPENDENT,
        subtotal_of: str | None = None,
        unknown_reason=None,
        token_role: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TokenQuantity:
        """Build a measured quantity whose accounting relationship is not trusted.

        This is deliberately separate from ``build_quantity``: a provider value can be an
        exact observation while still being unsafe to add. Keeping ``precision=exact`` and
        ``trust=unverified`` preserves that evidence in the uncertainty ceiling without
        allowing it into the canonical floor.
        """
        return TokenQuantity(
            token_type=token_type,
            quantity=quantity,
            precision_level=precision_level,
            usage_source=usage_source,
            additivity=Additivity.UNVERIFIED,
            overlap=overlap,
            trust=Trust.UNVERIFIED,
            subtotal_of=subtotal_of,
            unknown_reason=unknown_reason,
            token_role=token_role,
            metadata=metadata or {},
        )

    # --- to implement per provider ----------------------------------------------------
    def count_input_tokens(self, request: Any) -> int:
        """Count prompt tokens locally, before the call (for pre-flight / estimation)."""
        return estimate_tokens(str(request))

    @abstractmethod
    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        """Translate a full (non-streamed) provider response into a NormalizedUsage."""

    @abstractmethod
    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        """Translate one streamed event into usage, or None if it carries no usage yet."""

    def estimate_partial_output_tokens(self, accumulated_text: str) -> int:
        """Estimate output tokens from text seen so far (used on an interrupted stream)."""
        return estimate_tokens(accumulated_text)

    def reconcile_total(self, quantities: list[TokenQuantity], raw_total: int | None) -> int | None:
        """Return the raw provider_total_tokens to store (raw data; NEVER summed across events)."""
        return raw_total

    def classify_error(self, exc: Exception) -> str:
        """Map an extraction exception to a data-quality flag (e.g. ``normalization_error``)."""
        return "normalization_error"
