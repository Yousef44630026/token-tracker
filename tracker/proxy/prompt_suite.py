"""Prompt-suite parsing and command templating for repeatable proxy runs."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PromptCase:
    """One prompt to run through the proxy.

    ``prompt`` is used only to call the local client process. Persisted events receive
    only the label, sequence, source, and SHA-256 fingerprint.
    """

    sequence: int
    label: str
    prompt: str
    source: str | None = None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()

    @property
    def character_count(self) -> int:
        return len(self.prompt)


_FENCE_RE = re.compile(r"^\s*```(?P<language>[A-Za-z0-9_-]*)\s*$")
_NUMBERED_LABEL_RE = re.compile(r"^\s*(?P<number>\d+)\.\s+(?P<label>.+?)\s*:?\s*$")


def _clean_label(label: str, sequence: int) -> str:
    cleaned = " ".join(label.strip().rstrip(":").split())
    if not cleaned:
        return f"prompt-{sequence}"
    return cleaned[:120]


def parse_prompt_suite(path: str | os.PathLike[str]) -> list[PromptCase]:
    """Parse prompts from a Markdown suite.

    The preferred format is numbered sections followed by fenced ``text`` blocks.
    If no ``text`` fences are found, the whole file is treated as one prompt.
    """
    suite_path = Path(path)
    text = suite_path.read_text(encoding="utf-8")
    source = suite_path.name
    cases: list[PromptCase] = []
    current_label: str | None = None
    in_text_fence = False
    ignored_fence = False
    buffer: list[str] = []

    for line in text.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            if in_text_fence:
                prompt = "\n".join(buffer).strip()
                if prompt:
                    sequence = len(cases) + 1
                    cases.append(
                        PromptCase(
                            sequence=sequence,
                            label=_clean_label(current_label or "", sequence),
                            prompt=prompt,
                            source=source,
                        )
                    )
                buffer = []
                in_text_fence = False
                continue
            if ignored_fence:
                ignored_fence = False
                continue
            language = fence.group("language").lower()
            if language == "text":
                in_text_fence = True
                buffer = []
            else:
                ignored_fence = True
            continue

        if in_text_fence:
            buffer.append(line)
            continue
        if ignored_fence:
            continue

        label_match = _NUMBERED_LABEL_RE.match(line)
        if label_match:
            current_label = label_match.group("label")

    if in_text_fence:
        prompt = "\n".join(buffer).strip()
        if prompt:
            sequence = len(cases) + 1
            cases.append(
                PromptCase(
                    sequence=sequence,
                    label=_clean_label(current_label or "", sequence),
                    prompt=prompt,
                    source=source,
                )
            )

    if cases:
        return cases

    prompt = text.strip()
    if not prompt:
        return []
    return [
        PromptCase(
            sequence=1,
            label="prompt-1",
            prompt=prompt,
            source=source,
        )
    ]


def command_for_prompt(
    command: list[str],
    prompt: str,
    *,
    placeholder: str = "{prompt}",
) -> list[str]:
    """Return a child-process command with ``prompt`` injected safely as one arg."""
    if placeholder:
        replaced = [part.replace(placeholder, prompt) for part in command]
        if replaced != command:
            return replaced
    return [*command, prompt]
