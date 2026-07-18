"""Verification audit - documented usage fields are mapped or explicitly ignored.

Run: python tests/test_categorization_completeness.py

For every concrete provider adapter, the usage/token fields it documents are listed here.
Each field is either mapped to a TokenType, mapped to provider_total_tokens/metadata, or
listed in ALLOWED_IGNORED with a reason.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.anthropic_messages_adapter import AnthropicMessagesAdapter  # noqa: E402
from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_embeddings_adapter import AzureOpenAIEmbeddingsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.bedrock_converse_adapter import BedrockConverseAdapter  # noqa: E402
from tracker.adapters.bedrock_embeddings_adapter import BedrockEmbeddingsAdapter  # noqa: E402
from tracker.adapters.bedrock_invoke_model_adapter import BedrockInvokeModelAdapter  # noqa: E402
from tracker.adapters.cohere_chat_adapter import CohereChatAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.mistral_chat_adapter import MistralChatAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.openai_embeddings_adapter import OpenAIEmbeddingsAdapter  # noqa: E402
from tracker.adapters.openai_responses_adapter import OpenAIResponsesAdapter  # noqa: E402
from tracker.adapters.vertex_ai_generate_content_adapter import VertexAIGenerateContentAdapter  # noqa: E402
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(usage, token_type):
    return next((quantity for quantity in usage.quantities if quantity.token_type == token_type), None)


def openai_chat_response():
    return {
        "model": "gpt-4o-audit",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "prompt_tokens_details": {"cached_tokens": 40, "audio_tokens": 10},
            "completion_tokens_details": {
                "reasoning_tokens": 20,
                "audio_tokens": 5,
                "accepted_prediction_tokens": 3,
                "rejected_prediction_tokens": 2,
            },
        },
    }


def openai_responses_response():
    return {
        "model": "gpt-4o-audit",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 30,
            "total_tokens": 130,
            "input_tokens_details": {"cached_tokens": 40, "audio_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 20, "audio_tokens": 5},
        },
    }


def openai_embeddings_response():
    return {"model": "text-embedding-audit", "usage": {"prompt_tokens": 77, "total_tokens": 77}}


def mistral_response():
    return {
        "model": "mistral-audit",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
        },
    }


def anthropic_response():
    return {
        "model": "claude-audit",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 30,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 20,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 12,
                "ephemeral_1h_input_tokens": 8,
            },
            "output_tokens_details": {"thinking_tokens": 9},
        },
    }


def gemini_response():
    return {
        "modelVersion": "gemini-audit",
        "usageMetadata": {
            "promptTokenCount": 200,
            "candidatesTokenCount": 50,
            "cachedContentTokenCount": 25,
            "thoughtsTokenCount": 30,
            "totalTokenCount": 280,
            "promptTokensDetails": [
                {"modality": "TEXT", "tokenCount": 100},
                {"modality": "IMAGE", "tokenCount": 60},
                {"modality": "AUDIO", "tokenCount": 25},
                {"modality": "VIDEO", "tokenCount": 15},
            ],
            "candidatesTokensDetails": [
                {"modality": "TEXT", "tokenCount": 45},
                {"modality": "AUDIO", "tokenCount": 5},
            ],
        },
    }


def bedrock_converse_response():
    return {
        "modelId": "bedrock-audit",
        "usage": {
            "inputTokens": 100,
            "outputTokens": 30,
            "totalTokens": 190,
            "cacheReadInputTokens": 40,
            "cacheWriteInputTokens": 20,
        },
    }


def bedrock_invoke_response():
    return {
        "ResponseMetadata": {
            "HTTPHeaders": {
                "x-amzn-bedrock-input-token-count": "100",
                "x-amzn-bedrock-output-token-count": "30",
            }
        },
        "body_json": {
            "usage": {"inputTokens": 999, "outputTokens": 888, "totalTokens": 1887},
            "inputTextTokenCount": 777,
            "prompt_token_count": 666,
            "generation_token_count": 555,
        },
    }


def bedrock_embeddings_response():
    return {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": "77"}}}


def cohere_tokens_response():
    return {"model": "cohere-audit", "usage": {"tokens": {"input_tokens": 100, "output_tokens": 30}}}


def cohere_billed_response():
    return {
        "model": "cohere-audit",
        "usage": {"billed_units": {"input_tokens": 90, "output_tokens": 25, "search_units": 1}},
    }


def voyage_response():
    return {"model": "rerank-audit", "usage": {"total_tokens": 333}}


TOKEN_FIELDS = "token_fields"
TOKEN_AND_PROVIDER_TOTAL_FIELDS = "token_and_provider_total_fields"
PROVIDER_TOTAL_FIELDS = "provider_total_fields"
METADATA_FIELDS = "metadata_fields"
ALLOWED_IGNORED = "allowed_ignored"
DOCUMENTED_FIELDS = "documented_fields"
FULL_RESPONSE = "full_response"
ADAPTER = "adapter"


OPENAI_CHAT_FIELDS = {
    "usage.prompt_tokens": TokenType.INPUT,
    "usage.completion_tokens": TokenType.OUTPUT,
    "usage.prompt_tokens_details.cached_tokens": TokenType.CACHED_INPUT,
    "usage.prompt_tokens_details.audio_tokens": TokenType.AUDIO_INPUT,
    "usage.completion_tokens_details.reasoning_tokens": TokenType.REASONING,
    "usage.completion_tokens_details.audio_tokens": TokenType.AUDIO_OUTPUT,
}
OPENAI_CHAT_IGNORED = {
    "usage.completion_tokens_details.accepted_prediction_tokens": "prediction subtotals are already included in completion_tokens",
    "usage.completion_tokens_details.rejected_prediction_tokens": "prediction subtotals are already included in completion_tokens",
}

OPENAI_RESPONSES_FIELDS = {
    "usage.input_tokens": TokenType.INPUT,
    "usage.output_tokens": TokenType.OUTPUT,
    "usage.input_tokens_details.cached_tokens": TokenType.CACHED_INPUT,
    "usage.input_tokens_details.audio_tokens": TokenType.AUDIO_INPUT,
    "usage.output_tokens_details.reasoning_tokens": TokenType.REASONING,
    "usage.output_tokens_details.audio_tokens": TokenType.AUDIO_OUTPUT,
}

GEMINI_FIELDS = {
    "usageMetadata.promptTokenCount": TokenType.INPUT,
    "usageMetadata.candidatesTokenCount": TokenType.OUTPUT,
    "usageMetadata.cachedContentTokenCount": TokenType.CACHED_INPUT,
    "usageMetadata.thoughtsTokenCount": TokenType.THINKING,
    "usageMetadata.promptTokensDetails[IMAGE].tokenCount": TokenType.IMAGE_INPUT,
    "usageMetadata.promptTokensDetails[AUDIO].tokenCount": TokenType.AUDIO_INPUT,
    "usageMetadata.promptTokensDetails[VIDEO].tokenCount": TokenType.VIDEO_INPUT,
    "usageMetadata.candidatesTokensDetails[AUDIO].tokenCount": TokenType.AUDIO_OUTPUT,
}
GEMINI_IGNORED = {
    "usageMetadata.promptTokensDetails[TEXT].tokenCount": "TEXT detail duplicates promptTokenCount",
    "usageMetadata.candidatesTokensDetails[TEXT].tokenCount": "TEXT detail duplicates candidatesTokenCount",
}


SPECS = {
    "openai_chat_completions": {
        ADAPTER: OpenAIChatCompletionsAdapter,
        FULL_RESPONSE: openai_chat_response,
        TOKEN_FIELDS: OPENAI_CHAT_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
        ALLOWED_IGNORED: OPENAI_CHAT_IGNORED,
    },
    "azure_openai_chat_completions": {
        ADAPTER: AzureOpenAIChatCompletionsAdapter,
        FULL_RESPONSE: openai_chat_response,
        TOKEN_FIELDS: OPENAI_CHAT_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
        ALLOWED_IGNORED: OPENAI_CHAT_IGNORED,
    },
    "mistral_chat_completions": {
        ADAPTER: MistralChatAdapter,
        FULL_RESPONSE: mistral_response,
        TOKEN_FIELDS: {
            "usage.prompt_tokens": TokenType.INPUT,
            "usage.completion_tokens": TokenType.OUTPUT,
        },
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
    },
    "openai_responses": {
        ADAPTER: OpenAIResponsesAdapter,
        FULL_RESPONSE: openai_responses_response,
        TOKEN_FIELDS: OPENAI_RESPONSES_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
    },
    "azure_openai_responses": {
        ADAPTER: AzureOpenAIResponsesAdapter,
        FULL_RESPONSE: openai_responses_response,
        TOKEN_FIELDS: OPENAI_RESPONSES_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
    },
    "openai_embeddings": {
        ADAPTER: OpenAIEmbeddingsAdapter,
        FULL_RESPONSE: openai_embeddings_response,
        TOKEN_FIELDS: {"usage.prompt_tokens": TokenType.EMBEDDING},
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
    },
    "azure_openai_embeddings": {
        ADAPTER: AzureOpenAIEmbeddingsAdapter,
        FULL_RESPONSE: openai_embeddings_response,
        TOKEN_FIELDS: {"usage.prompt_tokens": TokenType.EMBEDDING},
        PROVIDER_TOTAL_FIELDS: {"usage.total_tokens"},
    },
    "anthropic_messages": {
        ADAPTER: AnthropicMessagesAdapter,
        FULL_RESPONSE: anthropic_response,
        TOKEN_FIELDS: {
            "usage.input_tokens": TokenType.INPUT,
            "usage.output_tokens": TokenType.OUTPUT,
            "usage.cache_read_input_tokens": TokenType.CACHED_INPUT,
            "usage.cache_creation_input_tokens": TokenType.CACHE_CREATION_INPUT,
            "usage.output_tokens_details.thinking_tokens": TokenType.THINKING,
        },
        METADATA_FIELDS: {
            "usage.cache_creation.ephemeral_5m_input_tokens": (TokenType.CACHE_CREATION_INPUT, "ephemeral_5m_input_tokens"),
            "usage.cache_creation.ephemeral_1h_input_tokens": (TokenType.CACHE_CREATION_INPUT, "ephemeral_1h_input_tokens"),
        },
    },
    "gemini_generate_content": {
        ADAPTER: GeminiGenerateContentAdapter,
        FULL_RESPONSE: gemini_response,
        TOKEN_FIELDS: GEMINI_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usageMetadata.totalTokenCount"},
        ALLOWED_IGNORED: GEMINI_IGNORED,
    },
    "vertex_ai_generate_content": {
        ADAPTER: VertexAIGenerateContentAdapter,
        FULL_RESPONSE: gemini_response,
        TOKEN_FIELDS: GEMINI_FIELDS,
        PROVIDER_TOTAL_FIELDS: {"usageMetadata.totalTokenCount"},
        ALLOWED_IGNORED: GEMINI_IGNORED,
    },
    "bedrock_converse": {
        ADAPTER: BedrockConverseAdapter,
        FULL_RESPONSE: bedrock_converse_response,
        TOKEN_FIELDS: {
            "usage.inputTokens": TokenType.INPUT,
            "usage.outputTokens": TokenType.OUTPUT,
            "usage.cacheReadInputTokens": TokenType.CACHED_INPUT,
            "usage.cacheWriteInputTokens": TokenType.CACHE_CREATION_INPUT,
        },
        PROVIDER_TOTAL_FIELDS: {"usage.totalTokens"},
    },
    "bedrock_invoke_model": {
        ADAPTER: BedrockInvokeModelAdapter,
        FULL_RESPONSE: bedrock_invoke_response,
        TOKEN_FIELDS: {
            "ResponseMetadata.HTTPHeaders.x-amzn-bedrock-input-token-count": TokenType.INPUT,
            "ResponseMetadata.HTTPHeaders.x-amzn-bedrock-output-token-count": TokenType.OUTPUT,
        },
        ALLOWED_IGNORED: {
            "body_json.usage.inputTokens": "InvokeModel adapter uses model-agnostic Bedrock token headers",
            "body_json.usage.outputTokens": "InvokeModel adapter uses model-agnostic Bedrock token headers",
            "body_json.usage.totalTokens": "InvokeModel adapter uses model-agnostic Bedrock token headers",
            "body_json.inputTextTokenCount": "model-specific body token fields are not authoritative for InvokeModel",
            "body_json.prompt_token_count": "model-specific body token fields are not authoritative for InvokeModel",
            "body_json.generation_token_count": "model-specific body token fields are not authoritative for InvokeModel",
        },
    },
    "bedrock_embeddings": {
        ADAPTER: BedrockEmbeddingsAdapter,
        FULL_RESPONSE: bedrock_embeddings_response,
        TOKEN_AND_PROVIDER_TOTAL_FIELDS: {
            "ResponseMetadata.HTTPHeaders.x-amzn-bedrock-input-token-count": TokenType.EMBEDDING,
        },
    },
    "cohere_chat_tokens": {
        ADAPTER: CohereChatAdapter,
        FULL_RESPONSE: cohere_tokens_response,
        TOKEN_FIELDS: {
            "usage.tokens.input_tokens": TokenType.INPUT,
            "usage.tokens.output_tokens": TokenType.OUTPUT,
        },
        ALLOWED_IGNORED: {},
    },
    "cohere_chat_billed_units_fallback": {
        ADAPTER: CohereChatAdapter,
        FULL_RESPONSE: cohere_billed_response,
        TOKEN_FIELDS: {
            "usage.billed_units.input_tokens": TokenType.INPUT,
            "usage.billed_units.output_tokens": TokenType.OUTPUT,
        },
        ALLOWED_IGNORED: {
            "usage.billed_units.search_units": "search_units are not tokens",
        },
    },
    "voyage_rerank": {
        ADAPTER: VoyageRerankAdapter,
        FULL_RESPONSE: voyage_response,
        TOKEN_AND_PROVIDER_TOTAL_FIELDS: {"usage.total_tokens": TokenType.RERANK_INPUT},
    },
}


for label, spec in SPECS.items():
    token_fields = spec.get(TOKEN_FIELDS, {})
    dual_fields = spec.get(TOKEN_AND_PROVIDER_TOTAL_FIELDS, {})
    provider_total_fields = set(spec.get(PROVIDER_TOTAL_FIELDS, set()))
    metadata_fields = spec.get(METADATA_FIELDS, {})
    allowed_ignored = spec.get(ALLOWED_IGNORED, {})
    documented_fields = set(spec.get(DOCUMENTED_FIELDS, set()))
    if not documented_fields:
        documented_fields = set(token_fields) | set(dual_fields) | provider_total_fields | set(metadata_fields) | set(allowed_ignored)

    accounted = set(token_fields) | set(dual_fields) | provider_total_fields | set(metadata_fields) | set(allowed_ignored)
    check(documented_fields == accounted, f"{label}: every documented token field is accounted for exactly once")
    for field in sorted(documented_fields):
        categories = [
            field in token_fields,
            field in dual_fields,
            field in provider_total_fields,
            field in metadata_fields,
            field in allowed_ignored,
        ]
        check(sum(1 for category in categories if category) == 1, f"{label}: {field} has one explicit category")
    for field, reason in sorted(allowed_ignored.items()):
        check(bool(reason), f"{label}: {field} ignored only with a reason")

    usage = spec[ADAPTER]().extract_usage_from_response(spec[FULL_RESPONSE]())
    expected_types = set(token_fields.values()) | set(dual_fields.values())
    actual_types = {quantity.token_type for quantity in usage.quantities}
    check(actual_types <= expected_types, f"{label}: adapter emitted no undocumented token types")
    for field, token_type in sorted(token_fields.items()):
        check(q(usage, token_type) is not None, f"{label}: {field} maps to {token_type.value}")
    for field, token_type in sorted(dual_fields.items()):
        check(q(usage, token_type) is not None, f"{label}: {field} maps to {token_type.value}")
        check(usage.provider_total_tokens is not None, f"{label}: {field} also maps to provider_total_tokens")
    for field in sorted(provider_total_fields):
        check(usage.provider_total_tokens is not None, f"{label}: {field} maps to provider_total_tokens")
    for field, (token_type, metadata_key) in sorted(metadata_fields.items()):
        quantity = q(usage, token_type)
        check(
            quantity is not None and metadata_key in quantity.metadata,
            f"{label}: {field} maps to {token_type.value} metadata[{metadata_key}]",
        )

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
