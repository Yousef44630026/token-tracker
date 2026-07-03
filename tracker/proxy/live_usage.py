"""Live token-budget progress rendering for proxy captures."""

from __future__ import annotations

from dataclasses import dataclass

from tracker.models.token_event import TokenEvent


def _format_int(value: int) -> str:
    return f"{value:,}"


@dataclass(slots=True)
class LiveUsageTracker:
    """Track provider-reported contributing tokens against an optional budget."""

    budget_tokens: int | None = None
    used_tokens: int = 0
    width: int = 28

    def __post_init__(self) -> None:
        if self.budget_tokens is not None:
            if isinstance(self.budget_tokens, bool) or not isinstance(self.budget_tokens, int) or self.budget_tokens <= 0:
                raise ValueError("budget_tokens must be a positive integer or None")
        if isinstance(self.used_tokens, bool) or not isinstance(self.used_tokens, int):
            raise TypeError("used_tokens must be an integer")
        if self.used_tokens < 0:
            raise ValueError("used_tokens cannot be negative")
        if isinstance(self.width, bool) or not isinstance(self.width, int):
            raise TypeError("width must be an integer")
        if self.width < 8:
            raise ValueError("width must be at least 8")

    def observe(self, event: TokenEvent) -> int:
        """Add one event's authoritative contributing tokens and return the delta."""
        delta = event.event_contributing_tokens
        self.used_tokens += delta
        return delta

    def render(self, *, delta: int | None = None) -> str:
        """Return a compact one-line progress bar."""
        delta_text = f" +{_format_int(delta)}" if delta is not None else ""
        if self.budget_tokens is None:
            return f"usage tokens: used={_format_int(self.used_tokens)}{delta_text}"

        ratio = min(self.used_tokens / self.budget_tokens, 1.0)
        filled = round(ratio * self.width)
        bar = "#" * filled + "-" * (self.width - filled)
        left = max(self.budget_tokens - self.used_tokens, 0)
        percent = round(self.used_tokens / self.budget_tokens * 100, 2)
        over = max(self.used_tokens - self.budget_tokens, 0)
        over_text = f" over={_format_int(over)}" if over else ""
        return (
            f"usage [{bar}] "
            f"used={_format_int(self.used_tokens)}/{_format_int(self.budget_tokens)} "
            f"left={_format_int(left)} "
            f"({percent}%){delta_text}{over_text}"
        )
