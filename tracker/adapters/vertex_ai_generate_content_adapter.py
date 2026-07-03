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
