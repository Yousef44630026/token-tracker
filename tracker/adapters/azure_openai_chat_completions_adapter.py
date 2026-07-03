"""Azure OpenAI Chat Completions adapter. (Phase 5)

Azure OpenAI is the OpenAI API hosted on Azure: the Chat Completions `usage` payload is
identical, so this reuses the OpenAI extraction wholesale and only changes ``provider`` to
"azure_openai" (aliased back to "openai" by the additivity table, INV-4). The provider label
is what distinguishes Azure usage from direct-OpenAI usage in reports.

Note: the response body carries the underlying ``model``, not the Azure *deployment name*
(which lives in the request URL). Pass the deployment name to the adapter constructor to
store it separately in quantity metadata without overwriting ``model``.
"""

from __future__ import annotations

from typing import Any

from tracker.adapters.azure_openai_common import AzureDeploymentMixin
from tracker.adapters.base import NormalizedUsage
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter


class AzureOpenAIChatCompletionsAdapter(AzureDeploymentMixin, OpenAIChatCompletionsAdapter):
    """Adapter for the Azure OpenAI Chat Completions API surface (same wire format)."""

    provider = "azure_openai"
    api_surface = "chat_completions"

    def extract_usage_from_response(self, response: Any) -> NormalizedUsage:
        return self._annotate_azure_deployment(super().extract_usage_from_response(response))

    def extract_usage_from_stream_event(self, event: Any) -> NormalizedUsage | None:
        usage = super().extract_usage_from_stream_event(event)
        return self._annotate_azure_deployment(usage) if usage is not None else None
