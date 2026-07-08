"""Registered data-quality flags and cardinality control."""

from __future__ import annotations

from collections.abc import Iterable

from tracker.models.enums import DataQualityFlag


def normalize_quality_flag(flag: str | DataQualityFlag) -> str:
    """Return a registered flag value, capping unknown labels to ``custom``."""
    if isinstance(flag, DataQualityFlag):
        return flag.value
    try:
        return DataQualityFlag(str(flag)).value
    except ValueError:
        return DataQualityFlag.CUSTOM.value


def normalize_quality_flags(flags: Iterable[str | DataQualityFlag]) -> list[str]:
    """Normalize and de-duplicate flags while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for flag in flags:
        value = normalize_quality_flag(flag)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


__all__ = ["normalize_quality_flag", "normalize_quality_flags"]
