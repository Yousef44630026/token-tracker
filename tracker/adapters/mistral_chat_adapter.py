"""Mistral Chat adapter. (additional provider)

Mistral's chat completions response is OpenAI-compatible (`usage.prompt_tokens /
completion_tokens / total_tokens`), so this reuses the OpenAI Chat extraction and only changes
``provider`` to "mistral" (registered explicitly in the INV-4 table). Mistral has no cache /
reasoning / audio sub-details, so those simply stay absent.
"""

from __future__ import annotations

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter


class MistralChatAdapter(OpenAIChatCompletionsAdapter):
    """Adapter for the Mistral chat completions API surface (OpenAI-compatible usage)."""

    provider = "mistral"
    api_surface = "chat_completions"
