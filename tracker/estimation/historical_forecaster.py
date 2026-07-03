"""Small robust historical token forecaster."""

from __future__ import annotations

from collections.abc import Iterable
from statistics import median


def forecast_tokens(history: Iterable[int], *, window: int | None = None) -> int | None:
    """Forecast the next count as the rounded median of recent non-negative observations."""
    values = list(history)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise TypeError("history must contain integers")
    if any(value < 0 for value in values):
        raise ValueError("history cannot contain negative token counts")
    if window is not None:
        if window <= 0:
            raise ValueError("window must be positive")
        values = values[-window:]
    if not values:
        return None
    return round(median(values))


__all__ = ["forecast_tokens"]
