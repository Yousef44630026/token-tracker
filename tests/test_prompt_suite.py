"""Prompt-suite parsing and command templating."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.proxy.cli import _completed_prompt_keys, _parser, main  # noqa: E402
from tracker.proxy.prompt_suite import command_for_prompt, parse_prompt_suite  # noqa: E402
from tracker.proxy.quality import check_prompt_output  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


cases = parse_prompt_suite("RELIABILITY_TEST.md")
check(len(cases) == 6, "Markdown suite exposes the six text prompts")
check(cases[0].sequence == 1 and cases[0].label == "Minimal output", "first label parsed")
check(cases[-1].label == "Multi-file reasoning", "last label parsed")
check(all(case.fingerprint and len(case.fingerprint) == 64 for case in cases), "hashes exist")

template_command = ["claude", "-p", "{prompt}", "--safe-mode"]
check(
    command_for_prompt(template_command, "hello")
    == [
        "claude",
        "-p",
        "hello",
        "--safe-mode",
    ],
    "placeholder is replaced as one argument",
)
check(
    command_for_prompt(["claude", "-p"], "hello") == ["claude", "-p", "hello"],
    "prompt appends when no placeholder is present",
)

parsed = _parser().parse_args(
    [
        "prompt-suite",
        "--provider",
        "anthropic",
        "--prompts",
        "RELIABILITY_TEST.md",
        "--dry-run",
    ]
)
check(parsed.mode == "prompt-suite" and parsed.port == 0, "prompt-suite defaults to auto port")
check(parsed.dry_run is True, "prompt-suite dry-run parses")
resume_args = _parser().parse_args(
    [
        "prompt-suite",
        "--provider",
        "anthropic",
        "--prompts",
        "RELIABILITY_TEST.md",
        "--start",
        "3",
        "--limit",
        "1",
        "--dry-run",
    ]
)
check(resume_args.start == 3 and resume_args.limit == 1, "prompt-suite resume parses")
resume_complete_args = _parser().parse_args(
    [
        "prompt-suite",
        "--provider",
        "anthropic",
        "--prompts",
        "RELIABILITY_TEST.md",
        "--resume-complete",
        "--dry-run",
    ]
)
check(resume_complete_args.resume_complete is True, "prompt-suite auto-resume parses")
quality_args = _parser().parse_args(
    [
        "prompt-suite",
        "--provider",
        "anthropic",
        "--prompts",
        "RELIABILITY_TEST.md",
        "--quality-checks",
        "--fail-on-quality",
        "--dry-run",
    ]
)
check(
    quality_args.quality_checks is True and quality_args.fail_on_quality is True,
    "prompt-suite quality flags parse",
)

scratch = os.path.join(os.getcwd(), ".test_prompt_suite_resume.jsonl")
try:
    os.remove(scratch)
except OSError:
    pass
repo = FileRepository(scratch)
first = cases[0]
repo.append(
    TokenEvent(
        event_id="complete-suite-event",
        request_correlation_id="req-complete-suite-event",
        trace_id="trace-suite",
        span_id="span-suite",
        provider="anthropic",
        model="claude-test",
        api_surface="messages",
        quantities=[
            TokenQuantity(
                TokenType.INPUT,
                1,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        observation={
            "status": "complete",
            "authoritative": True,
            "suite_prompt_sequence": first.sequence,
            "suite_prompt_label": first.label,
            "suite_prompt_fingerprint": first.fingerprint,
        },
    )
)
check(
    _completed_prompt_keys(repo) == {(first.sequence, first.fingerprint)},
    "auto-resume detects completed prompt keys",
)
try:
    os.remove(scratch)
except OSError:
    pass
check(
    main(
        [
            "prompt-suite",
            "--provider",
            "anthropic",
            "--prompts",
            "RELIABILITY_TEST.md",
            "--limit",
            "1",
            "--dry-run",
        ]
    )
    == 0,
    "prompt-suite dry-run executes without provider calls",
)
quality = check_prompt_output(
    sequence=1,
    label="Minimal deterministic output",
    stdout='{"result": "OK"}\n',
)
check(quality.passed is True, "quality checker extracts Claude JSON result")
bad_quality = check_prompt_output(
    sequence=1,
    label="Minimal deterministic output",
    stdout='{"result": "not ok"}\n',
)
check(bad_quality.passed is False, "quality checker catches deterministic mismatch")

quality_store = os.path.join(os.getcwd(), ".test_prompt_suite_quality.jsonl")
try:
    os.remove(quality_store)
except OSError:
    pass
check(
    main(
        [
            "prompt-suite",
            "--provider",
            "anthropic",
            "--store",
            quality_store,
            "--prompts",
            "RELIABILITY_TEST.md",
            "--limit",
            "1",
            "--quality-checks",
            "--fail-on-quality",
            "--no-report",
            "--",
            sys.executable,
            "-c",
            "import json; print(json.dumps({'result':'OK'}))",
        ]
    )
    == 0,
    "prompt-suite quality check executes against child stdout without provider calls",
)
try:
    os.remove(quality_store)
except OSError:
    pass

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
