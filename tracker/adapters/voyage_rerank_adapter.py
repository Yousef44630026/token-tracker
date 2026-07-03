"""Voyage Rerank adapter. (rerank surface)

Reranking re-scores candidate documents against a query. Token-reporting rerank APIs (e.g.
Voyage) return the operation's token count in ``usage.total_tokens``. There is no generated
output, so this maps to a single ``rerank_input`` quantity (total_contributing — it IS the
cost), reconciling to total_tokens.

Note: not all rerank providers bill in tokens — Cohere Rerank bills in "search_units", which
are NOT tokens and are out of scope for a token tracker.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.base import BaseAPISurfaceAdapter, NormalizedUsage
from tracker.adapters.base import field_value as _field
from tracker.models.enums import PrecisionLevel, TokenType, UsageSource


class VoyageRerankAdapter(BaseAPISurfaceAdapter):
    """Adapter for the Voyage rerank API surface (token-reporting rerank)."""

    provider = "voyage"
    api_surface = "rerank"

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        usage = _field(response, "usage")
        model = _field(response, "model")
        if not usage:
            return NormalizedUsage(
                provider=self.provider,
                api_surface=self.api_surface,
                model=model,
                data_quality_flags=["raw_usage_missing"],
            )
        total = _field(usage, "total_tokens")
        quantities = []
        if total is not None:
            quantities.append(self.build_quantity(TokenType.RERANK_INPUT, total, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE))
        return NormalizedUsage(
            provider=self.provider,
            api_surface=self.api_surface,
            model=model,
            quantities=quantities,
            provider_total_tokens=total,
            raw_usage=usage if isinstance(usage, dict) else None,
        )

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        # Reranking is a single-shot call, not streamed.
        return None
