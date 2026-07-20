"""Extra — exhaustive per-provider additivity table (INV-4).

Run: python tests/test_additivity_table.py

Pins every (provider, token_type) the table promises, the azure_openai alias, and the safe
fail-closed default (unverified, no parent) for anything unlisted.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, TokenType  # noqa: E402
from tracker.normalization.additivity import assign_additivity  # noqa: E402

_failures = 0
TC, SUB, UNV = Additivity.TOTAL_CONTRIBUTING, Additivity.SUBTOTAL_OF, Additivity.UNVERIFIED


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


cases = [
    # provider, surface, token_type, expected_additivity, expected_parent
    ("openai", "responses", TokenType.INPUT, TC, None),
    ("openai", "responses", TokenType.OUTPUT, TC, None),
    ("openai", "responses", TokenType.CACHED_INPUT, SUB, "input"),
    ("openai", "responses", TokenType.REASONING, SUB, "output"),
    ("openai", "chat_completions", TokenType.CACHED_INPUT, SUB, "input"),
    # azure is an alias of openai
    ("azure_openai", "responses", TokenType.CACHED_INPUT, SUB, "input"),
    ("azure_openai", "chat_completions", TokenType.REASONING, SUB, "output"),
    # gemini: thinking is total_contributing (added on top), cache is a subtotal
    ("gemini", "generate_content", TokenType.INPUT, TC, None),
    ("gemini", "generate_content", TokenType.THINKING, TC, None),
    ("gemini", "generate_content", TokenType.CACHED_INPUT, SUB, "input"),
    # Bedrock and Anthropic cache buckets are separate additive inputs.
    ("bedrock", "converse", TokenType.CACHED_INPUT, TC, None),
    ("bedrock", "converse", TokenType.CACHE_CREATION_INPUT, TC, None),
    ("anthropic", "messages", TokenType.CACHED_INPUT, TC, None),
    ("anthropic", "messages", TokenType.CACHE_CREATION_INPUT, TC, None),
    ("anthropic", "messages", TokenType.THINKING, SUB, "output"),
    # embeddings: the embedded tokens are total_contributing (registered explicitly)
    ("openai", "embeddings", TokenType.EMBEDDING, TC, None),
    ("azure_openai", "embeddings", TokenType.EMBEDDING, TC, None),
    # multimodal breakdown: audio/image/video are subtotals of input/output
    ("openai", "chat_completions", TokenType.AUDIO_INPUT, SUB, "input"),
    ("openai", "chat_completions", TokenType.AUDIO_OUTPUT, SUB, "output"),
    ("gemini", "generate_content", TokenType.IMAGE_INPUT, SUB, "input"),
    ("gemini", "generate_content", TokenType.AUDIO_INPUT, SUB, "input"),
    ("gemini", "generate_content", TokenType.VIDEO_INPUT, SUB, "input"),
    ("gemini", "generate_content", TokenType.AUDIO_OUTPUT, SUB, "output"),
    # additional providers: Mistral/Cohere chat + Voyage rerank (registered explicitly)
    ("mistral", "chat_completions", TokenType.INPUT, TC, None),
    ("mistral", "chat_completions", TokenType.OUTPUT, TC, None),
    ("cohere", "chat", TokenType.INPUT, TC, None),
    ("cohere", "chat", TokenType.OUTPUT, TC, None),
    ("voyage", "rerank", TokenType.RERANK_INPUT, TC, None),
    # Vertex AI aliases to Gemini; Bedrock embeddings registered
    ("vertex_ai", "generate_content", TokenType.THINKING, TC, None),
    ("vertex_ai", "generate_content", TokenType.CACHED_INPUT, SUB, "input"),
    ("vertex_ai", "embeddings", TokenType.EMBEDDING, TC, None),
    ("bedrock", "embeddings", TokenType.EMBEDDING, TC, None),
    # still-unlisted combos fail closed as unverified / no parent
    ("openai", "responses", TokenType.IMAGE_INPUT, UNV, None),
    ("openai", "responses", TokenType.VIDEO_INPUT, UNV, None),
    ("unknown_provider", "chat", TokenType.OUTPUT, UNV, None),
    ("anthropic", "messages", TokenType.AUDIO_INPUT, UNV, None),
]

for provider, surface, tt, exp_add, exp_parent in cases:
    add, parent = assign_additivity(provider, surface, tt)
    check(
        add == exp_add and parent == exp_parent,
        f"{provider}/{tt.value} -> {exp_add.value}, parent={exp_parent!r} (got {add.value}, {parent!r})",
    )

# token_type passed as a raw string still resolves
add, parent = assign_additivity("openai", "responses", "cached_input")
check(add == SUB and parent == "input", "string token_type resolves through the table")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
