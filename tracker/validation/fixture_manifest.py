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
from tracker.adapters.vertex_ai_embeddings_adapter import VertexAIEmbeddingsAdapter
from tracker.adapters.vertex_ai_generate_content_adapter import VertexAIGenerateContentAdapter
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter
from tracker.analytics.provider_validation import (
    CapabilityPolicy,
    FixtureValidationRecord,
    records_from_fixture_map,
)

REALISTIC_FIXTURE_ADAPTERS: dict[str, type[BaseAPISurfaceAdapter]] = {
    "anthropic_messages_cache.REAL.json": AnthropicMessagesAdapter,
    "anthropic_messages_full.SIMULATED.json": AnthropicMessagesAdapter,
    "azure_chat_content_filter.SIMULATED.json": AzureOpenAIChatCompletionsAdapter,
    "azure_openai_embeddings.SIMULATED.json": AzureOpenAIEmbeddingsAdapter,
    "azure_openai_responses.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_cache_behavior_call1.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_cache_behavior_call2.REAL.json": AzureOpenAIResponsesAdapter,
    "azure_content_filter_block_completed.REAL.json": AzureOpenAIResponsesAdapter,
    # Azure confrontation matrix (gpt-5-mini, /openai/v1) — captured by examples/azure_matrix_*.
    "azure_A1_simple.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A2_cache_call1.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A3_cache_call2.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A4_reasoning.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A5_cache_plus_reasoning.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A6_embeddings.REAL.json": AzureOpenAIEmbeddingsAdapter,
    "azure_A7_vision.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_A9_truncated.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_B1_stream_complete.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_B4_final_usage.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_E1_proxy_vs_direct_chat.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_E3_proxy_vs_direct_embeddings.REAL.json": AzureOpenAIEmbeddingsAdapter,
    "azure_rag_agent.REAL.json": AzureOpenAIChatCompletionsAdapter,
    "azure_rag_control.REAL.json": AzureOpenAIChatCompletionsAdapter,
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
    "vertex_ai_embeddings.SIMULATED.json": VertexAIEmbeddingsAdapter,
    "voyage_rerank_full.SIMULATED.json": VoyageRerankAdapter,
}

# Capability metadata is explicit. File names are provenance labels, not a reliable schema:
# several full fixtures contain non-zero cache buckets without "cache" in their names, and
# a captured terminal stream event is not necessarily named "stream".
CACHE_FIXTURE_NAMES = frozenset(
    {
        "anthropic_messages_cache.REAL.json",
        "anthropic_messages_full.SIMULATED.json",
        "azure_A2_cache_call1.REAL.json",
        "azure_A3_cache_call2.REAL.json",
        "azure_A5_cache_plus_reasoning.REAL.json",
        "azure_cache_behavior_call2.REAL.json",
        "bedrock_converse_full.SIMULATED.json",
        "gemini_generate_full.SIMULATED.json",
        "openai_chat_full.SIMULATED.json",
        "openai_responses_full.SIMULATED.json",
        "vertex_ai_generate_content.SIMULATED.json",
    }
)

STREAM_FIXTURE_NAMES = frozenset(
    {
        "azure_B1_stream_complete.REAL.json",
        "azure_B4_final_usage.REAL.json",
    }
)


# Release claims are explicit and capability-grained. A REAL fixture promotes only the
# capability it exercised; sharing adapter code never promotes a different cloud surface.
PROVIDER_CAPABILITY_POLICIES = (
    CapabilityPolicy("anthropic", "messages", "usage"),
    CapabilityPolicy("anthropic", "messages", "stream"),
    CapabilityPolicy("anthropic", "messages", "cache"),
    CapabilityPolicy("azure_openai", "chat_completions", "usage"),
    CapabilityPolicy("azure_openai", "chat_completions", "stream"),
    CapabilityPolicy("azure_openai", "chat_completions", "cache"),
    CapabilityPolicy("azure_openai", "embeddings", "usage"),
    CapabilityPolicy("azure_openai", "responses", "usage"),
    CapabilityPolicy("azure_openai", "responses", "stream"),
    CapabilityPolicy("azure_openai", "responses", "cache"),
    CapabilityPolicy("bedrock", "converse", "usage"),
    CapabilityPolicy("bedrock", "converse", "stream"),
    CapabilityPolicy("bedrock", "converse", "cache"),
    CapabilityPolicy(
        "bedrock",
        "invoke_model",
        "usage",
        note="Model-body contracts only; no universal token-count response headers.",
    ),
    CapabilityPolicy("bedrock", "invoke_model", "stream"),
    CapabilityPolicy(
        "bedrock",
        "invoke_model",
        "universal_header_token_counts",
        supported=False,
        note="AWS InvokeModel does not document universal token-count response headers.",
    ),
    CapabilityPolicy(
        "bedrock",
        "embeddings",
        "usage",
        note="Exact only when the selected model response reports a token count.",
    ),
    CapabilityPolicy(
        "bedrock",
        "embeddings",
        "cohere_response_token_count",
        supported=False,
        note="Cohere Embed on Bedrock does not return a provider token count.",
    ),
    CapabilityPolicy("cohere", "chat", "usage"),
    CapabilityPolicy("cohere", "chat", "stream"),
    CapabilityPolicy("gemini", "generate_content", "usage"),
    CapabilityPolicy("gemini", "generate_content", "stream"),
    CapabilityPolicy("gemini", "generate_content", "cache"),
    CapabilityPolicy("mistral", "chat_completions", "usage"),
    CapabilityPolicy("mistral", "chat_completions", "stream"),
    CapabilityPolicy("openai", "chat_completions", "usage"),
    CapabilityPolicy("openai", "chat_completions", "stream"),
    CapabilityPolicy("openai", "chat_completions", "cache"),
    CapabilityPolicy("openai", "embeddings", "usage"),
    CapabilityPolicy("openai", "responses", "usage"),
    CapabilityPolicy("openai", "responses", "stream"),
    CapabilityPolicy("openai", "responses", "cache"),
    CapabilityPolicy("vertex_ai", "generate_content", "usage"),
    CapabilityPolicy("vertex_ai", "generate_content", "stream"),
    CapabilityPolicy("vertex_ai", "generate_content", "cache"),
    CapabilityPolicy("vertex_ai", "embeddings", "usage"),
    CapabilityPolicy("voyage", "rerank", "usage"),
)


def realistic_fixture_records() -> list[FixtureValidationRecord]:
    return records_from_fixture_map(
        REALISTIC_FIXTURE_ADAPTERS,
        cache_fixture_names=CACHE_FIXTURE_NAMES,
        stream_fixture_names=STREAM_FIXTURE_NAMES,
    )


__all__ = [
    "CACHE_FIXTURE_NAMES",
    "PROVIDER_CAPABILITY_POLICIES",
    "REALISTIC_FIXTURE_ADAPTERS",
    "STREAM_FIXTURE_NAMES",
    "realistic_fixture_records",
]
