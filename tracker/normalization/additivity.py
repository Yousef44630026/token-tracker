"""Per-provider additivity assignment (INV-4). (Phase 3)

Additivity is *adapter-assigned*, never inferred from the token_type string. This module
is the single, centralized truth table the adapters call so every surface agrees:

  - OpenAI Responses & Chat Completions:
        input, output            -> total_contributing
        cached_input             -> subtotal_of "input"
        reasoning                -> subtotal_of "output"
  - Gemini Generate Content:
        input, output            -> total_contributing
        cached_input             -> subtotal_of "input"
        thinking                 -> total_contributing   (added ON TOP of output)
  - Anthropic Messages:
        input, cache read, cache creation, output
                                 -> total_contributing
        Anthropic reports these as distinct usage buckets; cache fields are not
        contained within ``input_tokens``.
  - Bedrock Converse:
        cache fields             -> unverified           (contribute 0, raise a flag)
                                    until verified against a real payload.

Anything not in the table defaults to ``unverified``. New providers and token fields must
be registered explicitly before they can affect totals; silently counting an unfamiliar
field is a double-counting risk. ``subtotal_of`` is a single parent string.
"""

from __future__ import annotations

from tracker.models.enums import Additivity, TokenType

# (provider, token_type) -> (additivity, subtotal_of parent)
_TABLE: dict[tuple[str, TokenType], tuple[Additivity, str | None]] = {
    # --- OpenAI (Responses + Chat Completions share the same truth) ---
    ("openai", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("openai", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("openai", TokenType.CACHED_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("openai", TokenType.REASONING): (Additivity.SUBTOTAL_OF, "output"),
    # embeddings surface: the embedded tokens ARE the billable cost (no output)
    ("openai", TokenType.EMBEDDING): (Additivity.TOTAL_CONTRIBUTING, None),
    # multimodal breakdown: audio tokens are a subtotal of input / output
    ("openai", TokenType.AUDIO_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("openai", TokenType.AUDIO_OUTPUT): (Additivity.SUBTOTAL_OF, "output"),
    # --- Gemini Generate Content ---
    ("gemini", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("gemini", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("gemini", TokenType.CACHED_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("gemini", TokenType.THINKING): (Additivity.TOTAL_CONTRIBUTING, None),
    # multimodal breakdown: per-modality counts are subtotals of input / output
    ("gemini", TokenType.IMAGE_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("gemini", TokenType.AUDIO_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("gemini", TokenType.VIDEO_INPUT): (Additivity.SUBTOTAL_OF, "input"),
    ("gemini", TokenType.AUDIO_OUTPUT): (Additivity.SUBTOTAL_OF, "output"),
    # --- Bedrock cache fields: still unverified until a real payload proves them ---
    ("bedrock", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("bedrock", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("bedrock", TokenType.CACHED_INPUT): (Additivity.UNVERIFIED, "input"),
    ("bedrock", TokenType.CACHE_CREATION_INPUT): (Additivity.UNVERIFIED, "input"),
    ("bedrock", TokenType.EMBEDDING): (Additivity.TOTAL_CONTRIBUTING, None),
    # Anthropic documents input/cache-read/cache-creation as separate input buckets.
    ("anthropic", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("anthropic", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("anthropic", TokenType.CACHED_INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("anthropic", TokenType.CACHE_CREATION_INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    # --- Mistral & Cohere chat: plain input/output (OpenAI-compatible accounting) ---
    ("mistral", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("mistral", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("cohere", TokenType.INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    ("cohere", TokenType.OUTPUT): (Additivity.TOTAL_CONTRIBUTING, None),
    # --- Rerank surfaces that report tokens (e.g. Voyage): the rerank tokens ARE the cost ---
    ("voyage", TokenType.RERANK_INPUT): (Additivity.TOTAL_CONTRIBUTING, None),
}

# Some providers share another's wire format — alias the provider key so one rule set serves
# both. Azure OpenAI IS OpenAI; Vertex AI serves the same Gemini models / usageMetadata.
_PROVIDER_ALIASES = {
    "azure_openai": "openai",
    "azure-openai": "openai",
    "azureopenai": "openai",
    "vertex_ai": "gemini",
    "vertex-ai": "gemini",
    "vertexai": "gemini",
}


def assign_additivity(provider: str, api_surface: str, token_type: TokenType) -> tuple[Additivity, str | None]:
    """Return ``(additivity, subtotal_of)`` for one quantity, per the provider table.

    ``api_surface`` is accepted for interface symmetry (and future surface-specific rules);
    today all surfaces of a given provider share one rule set. Unknown combinations fail
    closed as ``unverified`` so they are visible but excluded from totals.
    """
    key_provider = _PROVIDER_ALIASES.get(provider, provider)
    tt = token_type if isinstance(token_type, TokenType) else TokenType(token_type)
    return _TABLE.get((key_provider, tt), (Additivity.UNVERIFIED, None))
