"""Extra — version-drift defense: a provider renaming/dropping fields is DETECTED, not
silently miscounted.

Run: python tests/test_version_drift.py

The adapters pin documented field names. When a provider drifts (renames or drops a field),
the system must FAIL SAFE: either ``raw_usage_missing`` (nothing recognized) or
``provider_total_mismatch`` (a recognized subset disagrees with the provider total) — never a
confident-but-wrong total. This test pins that contract so a future drift is visible.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.cohere_chat_adapter import CohereChatAdapter  # noqa: E402
from tracker.adapters.gemini_generate_content_adapter import GeminiGenerateContentAdapter  # noqa: E402
from tracker.adapters.mistral_chat_adapter import MistralChatAdapter  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.voyage_rerank_adapter import VoyageRerankAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def flagged(ev):
    return bool({"raw_usage_missing", "provider_total_mismatch"} & set(ev.data_quality_flags))


# --- fully renamed usage: nothing recognized -> raw_usage_missing, contributes 0 ---
ev = normalize({"usage": {"input_token_count": 100, "output_token_count": 50}}, OpenAIChatCompletionsAdapter(), context=new_trace())
check(ev.event_contributing_tokens == 0, "OpenAI fully-renamed usage: contributes 0 (not a fabricated number)")
check("raw_usage_missing" in ev.data_quality_flags, "OpenAI fully-renamed usage: raw_usage_missing flagged")

# --- a token field drifts but the total survives -> provider_total_mismatch ---
ev = normalize(
    {"usage": {"prompt_tokens": 100, "total_tokens": 250}},  # completion_tokens dropped/renamed
    OpenAIChatCompletionsAdapter(),
    context=new_trace(),
)
check(ev.event_contributing_tokens == 100, "OpenAI dropped completion: only the recognized 100 counts")
check("provider_total_mismatch" in ev.data_quality_flags, "OpenAI dropped completion: mismatch (250 != 100) flagged")
check(ev.event_total_mismatch == 150, "OpenAI dropped completion: mismatch magnitude == 150 (visible)")

# --- same drift detection for Gemini ---
ev = normalize(
    {"usageMetadata": {"promptTokenCount": 1000, "totalTokenCount": 1600}},  # candidates dropped
    GeminiGenerateContentAdapter(),
    context=new_trace(),
)
check("provider_total_mismatch" in ev.data_quality_flags, "Gemini dropped candidates: mismatch flagged")
check(ev.event_total_mismatch == 600, "Gemini dropped candidates: mismatch magnitude == 600")

# --- the guarantee, stated directly: drift is never silently confident ---
drift_payloads = [
    {"usage": {"renamed_a": 1, "renamed_b": 2}},
    {"usage": {"prompt_tokens": 100, "total_tokens": 999}},
    {"usage": {"completion_tokens": 50, "total_tokens": 999}},
]
for p in drift_payloads:
    ev = normalize(p, OpenAIChatCompletionsAdapter(), context=new_trace())
    check(flagged(ev), f"drift payload is flagged, never silently miscounted: {list(p['usage'])}")

# --- provider-specific drift checks for Lot C providers ---
ev = normalize(
    {"usage": {"input_token_count": 100, "output_token_count": 50, "total_tokens": 150}}, MistralChatAdapter(), context=new_trace()
)
check("raw_usage_missing" in ev.data_quality_flags, "Mistral renamed usage: raw_usage_missing flagged")
check(ev.event_contributing_tokens == 0, "Mistral renamed usage: contributes 0")

ev = normalize({"usage": {"tokens": {"input_token_count": 100, "output_token_count": 50}}}, CohereChatAdapter(), context=new_trace())
check("raw_usage_missing" in ev.data_quality_flags, "Cohere renamed token fields: raw_usage_missing flagged")
check(ev.event_contributing_tokens == 0, "Cohere renamed token fields: contributes 0")

ev = normalize({"usage": {"totalTokenCount": 1500}}, VoyageRerankAdapter(), context=new_trace())
check("raw_usage_missing" in ev.data_quality_flags, "Voyage renamed total token field: raw_usage_missing flagged")
check(ev.event_contributing_tokens == 0, "Voyage renamed total token field: contributes 0")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
