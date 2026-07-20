"""Vertex AI generateContent adapter. (additional surface)

Vertex AI serves the same Gemini models and returns the same `usageMetadata` shape as the
Gemini Developer API, so this reuses the Gemini extraction and only changes ``provider`` to
"vertex_ai" (aliased back to "gemini" by the additivity table). The label distinguishes Vertex
usage from Developer-API usage in reports.
"""

from __future__ import annotations

from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter


class VertexAIGenerateContentAdapter(GeminiGenerateContentAdapter):
    """Adapter for the Vertex AI generateContent surface (same wire format as Gemini)."""

    provider = "vertex_ai"
    api_surface = "generate_content"

    def __init__(self, model_id: str | None = None) -> None:
        if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
            raise ValueError("model_id must be a non-empty string when provided")
        self.model_id = model_id.strip() if model_id is not None else None

    def extract_usage_from_response(self, response):
        usage = super().extract_usage_from_response(response)
        if usage.model is None:
            usage.model = self.model_id
        return usage

    def extract_usage_from_stream_event(self, event):
        usage = super().extract_usage_from_stream_event(event)
        if usage is not None and usage.model is None:
            usage.model = self.model_id
        return usage
