"""Privacy audit for proxy JSONL stores."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.proxy.privacy import audit_store, render_privacy_audit  # noqa: E402
from tracker.proxy.prompt_suite import parse_prompt_suite  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


clean_path = os.path.join(os.getcwd(), ".test_privacy_audit_clean.jsonl")
bad_path = os.path.join(os.getcwd(), ".test_privacy_audit_bad.jsonl")
for scratch in (clean_path, bad_path):
    try:
        os.remove(scratch)
    except OSError:
        pass

prompt = parse_prompt_suite("SCENARIO_PROMPTS.md")[0]
repo = FileRepository(clean_path)
repo.append(
    TokenEvent(
        event_id="privacy-clean",
        request_correlation_id="req-privacy-clean",
        trace_id="trace-privacy",
        span_id="span-privacy",
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
            "suite_prompt_sequence": prompt.sequence,
            "suite_prompt_label": prompt.label,
            "suite_prompt_fingerprint": prompt.fingerprint,
        },
    )
)
clean_result = audit_store(clean_path, prompts_path="SCENARIO_PROMPTS.md")
check(clean_result["passed"] is True, "clean event store passes privacy audit")
check(clean_result["prompt_count"] == 12, "privacy audit checks all scenario prompts")

fake_authorization = "Bearer " + "SECRET_TOKEN_" + "SHOULD_NOT_BE_STORED"
with open(bad_path, "w", encoding="utf-8") as handle:
    handle.write(
        json.dumps(
            {
                "authorization": fake_authorization,
                "raw_prompt": prompt.prompt,
            }
        )
        + "\n"
    )
bad_result = audit_store(bad_path, prompts_path="SCENARIO_PROMPTS.md")
finding_kinds = {finding["kind"] for finding in bad_result["findings"]}
check(bad_result["passed"] is False, "leaky store fails privacy audit")
check("secret_pattern" in finding_kinds, "secret-like text is detected")
check("raw_prompt" in finding_kinds, "raw prompt text is detected")
rendered = render_privacy_audit(bad_result)
check("SECRET_TOKEN_SHOULD_NOT_BE_STORED" not in rendered, "audit output redacts secrets")

for scratch in (clean_path, bad_path):
    try:
        os.remove(scratch)
    except OSError:
        pass

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
