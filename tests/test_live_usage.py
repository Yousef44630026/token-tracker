"""Live usage progress bars for proxy captures."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.proxy.cli import _parser, main  # noqa: E402
from tracker.proxy.live_usage import LiveUsageTracker  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def event(event_id, quantity, *, authoritative=True):
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"req-{event_id}",
        trace_id="trace-live",
        span_id=f"span-{event_id}",
        provider="anthropic",
        model="claude-test",
        api_surface="messages",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                quantity,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        observation={"status": "complete", "authoritative": authoritative},
    )


tracker = LiveUsageTracker(budget_tokens=100, width=10)
check("used=0/100" in tracker.render(), "initial budget bar renders")
delta = tracker.observe(event("e1", 25))
check(delta == 25 and tracker.used_tokens == 25, "authoritative event increments usage")
line = tracker.render(delta=delta)
check("[##" in line and "left=75" in line and "+25" in line, "usage bar shows left and delta")
delta = tracker.observe(event("e2", 999, authoritative=False))
check(delta == 0 and tracker.used_tokens == 25, "non-authoritative event contributes zero")

parsed = _parser().parse_args(
    [
        "prompt-suite",
        "--provider",
        "anthropic",
        "--prompts",
        "RELIABILITY_TEST.md",
        "--live-budget-tokens",
        "1000",
        "--live-bar-width",
        "12",
        "--dry-run",
    ]
)
check(
    parsed.live_budget_tokens == 1000 and parsed.live_bar_width == 12,
    "live budget CLI flags parse",
)
check(
    main(
        [
            "prompt-suite",
            "--provider",
            "anthropic",
            "--store",
            ".test_live_usage_dry_run.jsonl",
            "--prompts",
            "RELIABILITY_TEST.md",
            "--limit",
            "1",
            "--live-budget-tokens",
            "1000",
            "--dry-run",
        ]
    )
    == 0,
    "live budget dry-run executes without provider calls",
)

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
