"""TokenTap-compatible request text extraction and prompt estimation.

TokenTap counts the concatenated textual prompt with tiktoken's ``cl100k_base`` encoding.
That is useful as a repeatable comparison measurement, but it is not provider billing
usage. This module preserves that distinction and never stores the extracted prompt text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from tracker.estimation.local_tokenizer import estimate_tokens, estimate_with_metadata

TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class PromptEstimate:
    """One pre-flight estimate over extracted request text."""

    quantity: int
    estimator: str
    text_characters: int
    text_sha256: str

    def __post_init__(self) -> None:
        if self.quantity < 0 or self.text_characters < 0:
            raise ValueError("estimate values cannot be negative")


def _content_text(content: Any) -> str:
    """Match TokenTap's Anthropic content extraction."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    texts.append(item["text"])
                elif "content" in item:
                    texts.append(_content_text(item["content"]))
        return " ".join(texts)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _content_text(content["content"])
    return ""


def _anthropic_text(body: dict[str, Any]) -> str:
    texts: list[str] = []
    system = body.get("system")
    if system:
        texts.append(_content_text(system))
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            texts.append(_content_text(message.get("content", "")))
    return "\n".join(texts)


def _openai_chat_text(body: dict[str, Any]) -> str:
    """Match TokenTap's Chat Completions extraction."""
    texts: list[str] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            texts.append(" ".join(part for part in parts if isinstance(part, str)))
    return "\n".join(texts)


def _openai_responses_text(body: dict[str, Any]) -> str:
    """TokenTap-style extraction extended to the Responses API's ``input`` field."""
    texts: list[str] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str):
        texts.append(instructions)

    input_value = body.get("input")
    if isinstance(input_value, str):
        texts.append(input_value)
    elif isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                texts.append(_content_text(item.get("content", item.get("text", ""))))

    # Some OpenAI-compatible clients still send messages to a Responses endpoint.
    if body.get("messages"):
        texts.append(_openai_chat_text(body))
    return "\n".join(text for text in texts if text)


def extract_prompt_text(
    body: dict[str, Any],
    provider: str,
    api_surface: str,
) -> str:
    """Extract only textual prompt fields; omit credentials, images, and control fields."""
    if provider == "anthropic" and api_surface == "messages":
        return _anthropic_text(body)
    if provider == "openai" and api_surface == "chat_completions":
        return _openai_chat_text(body)
    if provider == "openai" and api_surface == "responses":
        return _openai_responses_text(body)
    return ""


def extract_latest_user_text(
    body: dict[str, Any],
    provider: str,
    api_surface: str,
) -> str:
    """Extract the latest human-authored text, excluding tool-result payloads."""
    messages = body.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content", "")
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                texts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") in {None, "text", "input_text"} and isinstance(part.get("text"), str)
                ]
                joined = "\n".join(text for text in texts if text)
                if joined:
                    return joined

    if provider == "openai" and api_surface == "responses":
        input_value = body.get("input")
        if isinstance(input_value, str):
            return input_value
        if isinstance(input_value, list):
            for item in reversed(input_value):
                if not isinstance(item, dict) or item.get("role") not in {None, "user"}:
                    continue
                text = _content_text(item.get("content", item.get("text", "")))
                if text:
                    return text
    return ""


@lru_cache(maxsize=1)
def _tokentap_counter() -> tuple[TokenCounter, str]:
    estimate = estimate_with_metadata("")
    return estimate_tokens, estimate.estimator


def estimate_prompt(
    body: dict[str, Any],
    provider: str,
    api_surface: str,
    *,
    counter: TokenCounter | None = None,
    estimator_name: str | None = None,
) -> PromptEstimate:
    """Return a comparison estimate without retaining the extracted prompt."""
    text = extract_prompt_text(body, provider, api_surface)
    return estimate_text(text, counter=counter, estimator_name=estimator_name)


def estimate_text(
    text: str,
    *,
    counter: TokenCounter | None = None,
    estimator_name: str | None = None,
) -> PromptEstimate:
    """Estimate one raw prompt text without retaining it."""
    if counter is None:
        counter, default_name = _tokentap_counter()
    else:
        default_name = "injected_test_counter"
    return PromptEstimate(
        quantity=counter(text),
        estimator=estimator_name or default_name,
        text_characters=len(text),
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
