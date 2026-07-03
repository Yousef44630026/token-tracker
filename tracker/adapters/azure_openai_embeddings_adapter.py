"""Azure OpenAI Embeddings adapter. (RAG)

Azure OpenAI embeddings return the same `usage` shape as OpenAI, so this reuses the OpenAI
extraction and only changes ``provider`` to "azure_openai" (aliased back to "openai" by the
additivity table). The provider label distinguishes Azure embedding usage in reports.
Pass the deployment name to the adapter constructor to store it separately in quantity
metadata without overwriting the response model.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.azure_openai_common import AzureDeploymentMixin
from tracker.adapters.base import NormalizedUsage
from tracker.adapters.openai_embeddings_adapter import OpenAIEmbeddingsAdapter


class AzureOpenAIEmbeddingsAdapter(AzureDeploymentMixin, OpenAIEmbeddingsAdapter):
    """Adapter for the Azure OpenAI Embeddings API surface (same wire format as OpenAI)."""

    provider = "azure_openai"
    api_surface = "embeddings"

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        return self._annotate_azure_deployment(super().extract_usage_from_response(response))

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = super().extract_usage_from_stream_event(event)
        return self._annotate_azure_deployment(usage) if usage is not None else None
