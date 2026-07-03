"""TokenQuantity — source of truth (INV-1) + derived view (INV-2). (Phase 2)

STORED (the only things serialized): token_type, token_role, quantity, precision_level,
usage_source, additivity, subtotal_of, aggregation_mode, unknown_reason, metadata.

DERIVED (@property, never stored, never serialized): included_in_total,
quantity_in_total, export_warning. These are recomputed on read so storage can never
disagree with the rules — see INV-2 / INV-4 / INV-6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tracker.models.enums import (
    Additivity,
    AggregationMode,
    PrecisionLevel,
    TokenType,
    UnknownReason,
    UsageSource,
)


@dataclass
class TokenQuantity:
    """One measured (or unmeasured) quantity of one token_type within an event."""

    # --- stored: source of truth (INV-1) ---
    token_type: TokenType
    quantity: int | None
    precision_level: PrecisionLevel
    usage_source: UsageSource
    additivity: Additivity
    aggregation_mode: AggregationMode = AggregationMode.SUM
    token_role: str | None = None
    subtotal_of: str | None = None
    unknown_reason: UnknownReason | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity is not None:
            if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
                raise TypeError("quantity must be an integer or None")
            if self.quantity < 0:
                raise ValueError("quantity cannot be negative")
        if self.quantity is None and self.precision_level != PrecisionLevel.UNKNOWN:
            raise ValueError("a missing quantity must have unknown precision")
        if self.quantity is not None and self.precision_level == PrecisionLevel.UNKNOWN:
            raise ValueError("unknown precision requires a missing quantity")
        if self.unknown_reason is not None and self.quantity is not None:
            raise ValueError("unknown_reason is only valid for a missing quantity")
        if self.additivity == Additivity.SUBTOTAL_OF and not self.subtotal_of:
            raise ValueError("subtotal_of additivity requires a parent token type")
        # Fail closed: MAX/LAST are reserved but the derivation only implements SUM
        # (quantity_in_total always sums). Refuse a mode the engine would silently ignore
        # rather than let the field promise behavior it does not honor. Lift this guard when
        # the derivation actually implements the other modes.
        if self.aggregation_mode != AggregationMode.SUM:
            raise ValueError(
                f"aggregation_mode {self.aggregation_mode.value!r} is reserved and not yet "
                "honored by the derivation (only SUM is implemented); refusing to store a mode "
                "the engine would silently ignore"
            )

    # --- derived: computed only (INV-2), never stored/serialized ---
    @property
    def included_in_total(self) -> bool:
        return self.additivity == Additivity.TOTAL_CONTRIBUTING and self.quantity is not None

    @property
    def quantity_in_total(self) -> int:
        return self.quantity if self.included_in_total else 0

    @property
    def export_warning(self) -> str | None:
        if self.additivity == Additivity.SUBTOTAL_OF:
            return "subtotal_excluded_from_total"
        if self.additivity == Additivity.UNVERIFIED:
            return "unverified_additivity_excluded_from_total"
        if self.quantity is None and self.precision_level == PrecisionLevel.UNKNOWN:
            return "unknown_quantity_excluded_from_total"
        return None

    # --- serialization: STORED fields only ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "token_type": self.token_type.value,
            "quantity": self.quantity,
            "precision_level": self.precision_level.value,
            "usage_source": self.usage_source.value,
            "additivity": self.additivity.value,
            "aggregation_mode": self.aggregation_mode.value,
            "token_role": self.token_role,
            "subtotal_of": self.subtotal_of,
            "unknown_reason": (self.unknown_reason.value if self.unknown_reason else None),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenQuantity:
        ur = d.get("unknown_reason")
        return cls(
            token_type=TokenType(d["token_type"]),
            quantity=d["quantity"],
            precision_level=PrecisionLevel(d["precision_level"]),
            usage_source=UsageSource(d["usage_source"]),
            additivity=Additivity(d["additivity"]),
            aggregation_mode=AggregationMode(d.get("aggregation_mode", "sum")),
            token_role=d.get("token_role"),
            subtotal_of=d.get("subtotal_of"),
            unknown_reason=UnknownReason(ur) if ur else None,
            metadata=d.get("metadata", {}),
        )
