"""Extra — historical token forecaster (estimation).

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_historical_forecaster.py

forecast_tokens returns the rounded median of recent non-negative integer observations, honors
a rolling window, and rejects bad input (negatives -> ValueError, non-ints/bools -> TypeError).
A forecast is an ESTIMATE: callers pair it with precision=estimate / usage_source=historical_forecast.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.estimation.historical_forecaster import forecast_tokens  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


# --- empty / single ---
check(forecast_tokens([]) is None, "no history -> None")
check(forecast_tokens([42]) == 42, "single observation -> itself")

# --- median over history ---
check(forecast_tokens([10, 20, 30]) == 20, "odd count -> median (20)")
check(forecast_tokens([10, 20, 30, 40]) == 25, "even count -> rounded mean of middle two (25)")
check(forecast_tokens([500, 100, 300, 200, 400]) == 300, "unordered -> median (300)")

# --- rolling window keeps only the most recent ---
check(forecast_tokens([10, 20, 30, 40, 1000], window=3) == 40, "window=3 -> median of last 3 (40)")
check(forecast_tokens([1, 1, 1, 999], window=2) == 500, "window=2 -> median of last 2 ([1,999] -> 500)")

# --- validation ---
for bad, exc, label in [
    (-1, ValueError, "negative observation -> ValueError"),
    (2.5, TypeError, "float observation -> TypeError"),
    (True, TypeError, "bool observation -> TypeError"),
]:
    raised = None
    try:
        forecast_tokens([10, bad])
    except Exception as e:  # noqa: BLE001
        raised = type(e)
    check(raised is exc, label)

raised = None
try:
    forecast_tokens([1, 2, 3], window=0)
except ValueError:
    raised = ValueError
check(raised is ValueError, "window <= 0 -> ValueError")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
