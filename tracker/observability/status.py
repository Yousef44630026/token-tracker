"""Shared operational observation status values."""

STATUS_VALUES = {
    "complete",
    "incomplete",
    "success",
    "failed",
    "error",
    "timeout",
    "timed_out",
    "rate_limited",
    "throttled",
    "fallback",
    "legacy",
    "unknown",
}

__all__ = ["STATUS_VALUES"]
