"""Azure OpenAI Responses adapter. (Phase 5)

Azure OpenAI is the OpenAI API hosted on Azure: the Responses `usage` payload is identical,
so this reuses the OpenAI extraction wholesale and only changes ``provider`` to
"azure_openai". That label is what tells Azure usage apart from direct-OpenAI usage in
reports; for additivity it is aliased back to "openai" by the central table (INV-4), so
cached/reasoning stay subtotals and nothing double counts.

Note: the response body carries the underlying ``model`` (e.g. gpt-4o), not the Azure
*deployment name* (that lives in the request URL). Pass the deployment name to the adapter
constructor to store it separately in quantity metadata without overwriting ``model``.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.azure_openai_common import AzureDeploymentMixin
from tracker.adapters.base import NormalizedUsage
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter


class AzureOpenAIResponsesAdapter(AzureDeploymentMixin, OpenAIResponsesAdapter):
    """Adapter for the Azure OpenAI Responses API surface (same wire format as OpenAI)."""

    provider = "azure_openai"
    api_surface = "responses"

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        return self._annotate_azure_deployment(super().extract_usage_from_response(response))

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = super().extract_usage_from_stream_event(event)
        return self._annotate_azure_deployment(usage) if usage is not None else None
