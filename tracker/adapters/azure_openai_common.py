"""Shared Azure OpenAI adapter helpers.

Azure OpenAI routes requests through deployment names, while response bodies usually carry
the underlying model name. The tracker keeps those two facts separate:

- ``NormalizedUsage.model`` remains the provider response model;
- ``metadata["azure_deployment"]`` records the Azure deployment/routing name when supplied.
"""

from __future__ import annotations

from tracker.adapters.base import NormalizedUsage


class AzureDeploymentMixin:
    """Annotate quantities with the Azure deployment without overwriting model."""

    def __init__(
        self,
        deployment: str | None = None,
        *,
        deployment_name: str | None = None,
    ) -> None:
        if deployment and deployment_name and deployment != deployment_name:
            raise ValueError("deployment and deployment_name disagree")
        self.azure_deployment = deployment or deployment_name

    def _annotate_azure_deployment(self, usage: NormalizedUsage) -> NormalizedUsage:
        if not self.azure_deployment:
            return usage
        for quantity in usage.quantities:
            quantity.metadata.setdefault("azure_deployment", self.azure_deployment)
        return usage
