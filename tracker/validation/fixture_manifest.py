"""Explicit fixture-to-adapter manifest used by audits and validation reports."""

from __future__ import annotations

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter
from tracker.adapters.azure_openai_chat_completions_adapter import (
    AzureOpenAIChatCompletionsAdapter,
)
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter
from tracker.adapters.base import BaseAPISurfaceAdapter
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter
from tracker.adapters.bedrock_embeddings_adapter import BedrockEmbeddingsAdapter
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter
from tracker.adapters.cohere_chat_adapter import CohereChatAdapter
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter
from tracker.adapters.mistral_chat_adapter import MistralChatAdapter
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter
from tracker.adapters.openai_embeddings_adapter import OpenAIEmbeddingsAdapter
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter
from tracker.adapters.vertex_ai_generate_content_adapter import VertexAIGenerateContentAdapter
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter
from tracker.analytics.provider_validation import (
    FixtureValidationRecord,
    records_from_fixture_map,
)

REALISTIC_FIXTURE_ADAPTERS: dict[str, type[BaseAPISurfaceAdapter]] = {
    "anthropic_messages_full.SIMULATED.json": AnthropicMessagesAdapter,
    "azure_chat_content_filter.SIMULATED.json": AzureOpenAIChatCompletionsAdapter,
    "azure_openai_embeddings.SIMULATED.json": AzureOpenAIEmbeddingsAdapter,
    "azure_openai_responses.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_cache_behavior_call1.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_cache_behavior_call2.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_content_filter_block_completed.REAL.json": AzureOpenAIResponsesAdapter,
    "bedrock_converse.REAL.json": BedrockConverseAdapter,
    "bedrock_converse_full.SIMULATED.json": BedrockConverseAdapter,
    "bedrock_embeddings_full.SIMULATED.json": BedrockEmbeddingsAdapter,
    "bedrock_invoke_model_body_variants.SIMULATED.json": BedrockInvokeModelAdapter,
    "bedrock_invoke_model_full.SIMULATED.json": BedrockInvokeModelAdapter,
    "cohere_chat_full.SIMULATED.json": CohereChatAdapter,
    "gemini_generate.REAL.json": GeminiGenerateContentAdapter,
    "gemini_generate_full.SIMULATED.json": GeminiGenerateContentAdapter,
    "gemini_multimodal.SIMULATED.json": GeminiGenerateContentAdapter,
    "mistral_chat_full.SIMULATED.json": MistralChatAdapter,
    "openai_chat_audio.SIMULATED.json": OpenAIChatCompletionsAdapter,
    "openai_chat_full.SIMULATED.json": OpenAIChatCompletionsAdapter,
    "openai_embeddings_full.SIMULATED.json": OpenAIEmbeddingsAdapter,
    "openai_responses_full.SIMULATED.json": OpenAIResponsesAdapter,
    "vertex_ai_generate_content.SIMULATED.json": VertexAIGenerateContentAdapter,
    "voyage_rerank_full.SIMULATED.json": VoyageRerankAdapter,
}


def realistic_fixture_records() -> list[FixtureValidationRecord]:
    return records_from_fixture_map(REALISTIC_FIXTURE_ADAPTERS)


__all__ = ["REALISTIC_FIXTURE_ADAPTERS", "realistic_fixture_records"]
