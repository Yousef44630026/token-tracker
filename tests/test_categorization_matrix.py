"""Verification audit - provider token categorization matrix.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_categorization_matrix.py

Pins the central additivity table as the audited source of truth. Unknown provider/type
pairs must fail closed as unverified, contribute 0, and raise the normalizer flag.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.normalization.additivity import _PROVIDER_ALIASES, _TABLE, assign_additivity  # noqa: E402
from tracker.normalization.data_quality import normalizer_flags  # noqa: E402

_failures = 0

TC = Additivity.TOTAL_CONTRIBUTING
SUB = Additivity.SUBTOTAL_OF
UNV = Additivity.UNVERIFIED


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


EXPECTED_TABLE = {
    ("openai", TokenType.INPUT): (TC, None),
    ("openai", TokenType.OUTPUT): (TC, None),
    ("openai", TokenType.CACHED_INPUT): (SUB, "input"),
    ("openai", TokenType.REASONING): (SUB, "output"),
    ("openai", TokenType.EMBEDDING): (TC, None),
    ("openai", TokenType.AUDIO_INPUT): (SUB, "input"),
    ("openai", TokenType.AUDIO_OUTPUT): (SUB, "output"),
    ("gemini", TokenType.INPUT): (TC, None),
    ("gemini", TokenType.OUTPUT): (TC, None),
    ("gemini", TokenType.CACHED_INPUT): (SUB, "input"),
    ("gemini", TokenType.THINKING): (TC, None),
    ("gemini", TokenType.IMAGE_INPUT): (SUB, "input"),
    ("gemini", TokenType.AUDIO_INPUT): (SUB, "input"),
    ("gemini", TokenType.VIDEO_INPUT): (SUB, "input"),
    ("gemini", TokenType.AUDIO_OUTPUT): (SUB, "output"),
    ("bedrock", TokenType.INPUT): (TC, None),
    ("bedrock", TokenType.OUTPUT): (TC, None),
    ("bedrock", TokenType.CACHED_INPUT): (TC, None),
    ("bedrock", TokenType.CACHE_CREATION_INPUT): (TC, None),
    ("bedrock", TokenType.EMBEDDING): (TC, None),
    ("anthropic", TokenType.INPUT): (TC, None),
    ("anthropic", TokenType.OUTPUT): (TC, None),
    ("anthropic", TokenType.CACHED_INPUT): (TC, None),
    ("anthropic", TokenType.CACHE_CREATION_INPUT): (TC, None),
    ("anthropic", TokenType.THINKING): (SUB, "output"),
    ("mistral", TokenType.INPUT): (TC, None),
    ("mistral", TokenType.OUTPUT): (TC, None),
    ("cohere", TokenType.INPUT): (TC, None),
    ("cohere", TokenType.OUTPUT): (TC, None),
    ("voyage", TokenType.RERANK_INPUT): (TC, None),
}


def quantity(provider, token_type, amount):
    additivity, subtotal_of = assign_additivity(provider, "audit", token_type)
    return TokenQuantity(
        token_type=token_type,
        quantity=amount,
        precision_level=PrecisionLevel.EXACT,
        usage_source=UsageSource.PROVIDER_RESPONSE,
        additivity=additivity,
        subtotal_of=subtotal_of,
    )


check(set(_TABLE) == set(EXPECTED_TABLE), "central _TABLE has exactly the audited provider/type entries")
for key, expected in sorted(EXPECTED_TABLE.items(), key=lambda item: (item[0][0], item[0][1].value)):
    actual = _TABLE.get(key)
    provider, token_type = key
    check(
        actual == expected,
        f"{provider}/{token_type.value}: additivity={expected[0].value}, subtotal_of={expected[1]!r}",
    )

EXPECTED_ALIASES = {
    "azure_openai": "openai",
    "azure-openai": "openai",
    "azureopenai": "openai",
    "vertex_ai": "gemini",
    "vertex-ai": "gemini",
    "vertexai": "gemini",
}
check(_PROVIDER_ALIASES == EXPECTED_ALIASES, "provider aliases are pinned for audited OpenAI/Gemini semantics")

for alias, canonical in sorted(EXPECTED_ALIASES.items()):
    for token_type in (TokenType.INPUT, TokenType.CACHED_INPUT):
        alias_result = assign_additivity(alias, "audit", token_type)
        canonical_result = assign_additivity(canonical, "audit", token_type)
        check(alias_result == canonical_result, f"{alias}/{token_type.value} resolves as {canonical}")

subtotal = quantity("openai", TokenType.CACHED_INPUT, 123)
contributing = quantity("gemini", TokenType.THINKING, 456)
check(subtotal.additivity == SUB and subtotal.quantity_in_total == 0, "subtotal_of quantity contributes 0")
check(contributing.additivity == TC and contributing.quantity_in_total == 456, "total_contributing quantity contributes its quantity")

unknown_additivity, unknown_parent = assign_additivity("unregistered_provider", "audit", TokenType.OUTPUT)
check(unknown_additivity == UNV and unknown_parent is None, "unregistered provider/type fails closed as unverified")

unverified = TokenQuantity(
    token_type=TokenType.OUTPUT,
    quantity=99,
    precision_level=PrecisionLevel.EXACT,
    usage_source=UsageSource.PROVIDER_RESPONSE,
    additivity=unknown_additivity,
    subtotal_of=unknown_parent,
)
check(unverified.quantity_in_total == 0, "unverified quantity contributes 0")
check("unverified_additivity" in normalizer_flags([unverified], None), "unverified quantity raises unverified_additivity")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
