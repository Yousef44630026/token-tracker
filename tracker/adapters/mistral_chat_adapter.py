"""Mistral Chat adapter. (additional provider)

Mistral's chat completions response is OpenAI-compatible (`usage.prompt_tokens /
completion_tokens / total_tokens`), so this reuses the OpenAI Chat extraction and only changes
``provider`` to "mistral".

The inherited extraction ALSO reads OpenAI-style sub-details
(``prompt_tokens_details.cached_tokens``, ``completion_tokens_details.reasoning_tokens``,
audio tokens) when a Mistral response carries them — it does NOT drop them. Mistral's
cache/reasoning additivity semantics are not yet verified against a recorded real payload,
so the INV-4 table registers only mistral input/output: any such sub-detail therefore FAILS
CLOSED to additivity="unverified" (contributes 0, raises ``unverified_additivity``) instead of
being assumed identical to OpenAI's ``subtotal_of`` semantics. Totals stay correct either way
(a subtotal and an unverified quantity both contribute 0). Register mistral cache/reasoning
rows only after a real Mistral payload confirms them. This behavior is pinned by
tests/test_mistral_detail_fields_fail_closed.py.
"""

from __future__ import annotations

from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter


class MistralChatAdapter(OpenAIChatCompletionsAdapter):
    """Adapter for the Mistral chat completions API surface (OpenAI-compatible usage)."""

    provider = "mistral"
    api_surface = "chat_completions"
