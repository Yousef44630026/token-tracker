"""Privacy audit helpers for proxy JSONL captures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tracker.proxy.prompt_suite import parse_prompt_suite

# severity="info": the pattern only proves auth-related VOCABULARY is present (a field name,
# a header name, a benign status word like "oauth" describing an auth METHOD) — real, secret-
# free operational data routinely contains these (e.g. observation.provider_error_code =
# "invalid_authorization", or observation.auth_method = "oauth"). These do NOT fail the audit
# on their own; they are surfaced for a human to glance at, not treated as a confirmed leak.
#
# severity="secret": the pattern requires an actual credential-SHAPED VALUE next to it, not
# just a keyword. These DO fail the audit.
#
# Found in review: the OLD single-tier design conflated the two (a legitimate "Authorization
# failed: invalid credentials" error message failed the audit), AND had no pattern at all for
# Azure/AWS/Google key shapes — a real Azure key (the exact shape leaked once already in this
# project's own session) or an AWS access key ID sailed through completely undetected.
_SECRET_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("info", "authorization_header", re.compile(r"\bauthorization\b", re.IGNORECASE)),
    ("secret", "bearer_token", re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)),
    ("info", "x_api_key_header", re.compile(r"\bx-api-key\b", re.IGNORECASE)),
    (
        "info",
        "api_key_name",
        re.compile(
            r"\b(?:api[_-]?key|openai_api_key|anthropic_api_key)\b",
            re.IGNORECASE,
        ),
    ),
    ("secret", "openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("info", "oauth_marker", re.compile(r"\boauth\b", re.IGNORECASE)),
    # Azure Cognitive Services / OpenAI keys: long (~80-100 char) contiguous alphanumeric runs
    # with no separators — distinctive enough not to collide with this project's own uuid4().hex
    # ids (32 chars) or hex hashes (32/40/64 chars).
    ("secret", "azure_key_shaped", re.compile(r"\b[A-Za-z0-9]{80,100}\b")),
    ("secret", "aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("secret", "google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(offset, 0)) + 1


def _finding(kind: str, *, line: int | None, detail: str, severity: str = "secret") -> dict[str, Any]:
    return {"kind": kind, "line": line, "detail": detail, "severity": severity}


def audit_store(
    store_path: str,
    *,
    prompts_path: str | None = None,
) -> dict[str, Any]:
    """Scan a proxy event JSONL for obvious secret or raw-prompt leakage.

    Findings intentionally do not include matched secret text.
    """
    path = Path(store_path)
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    findings: list[dict[str, Any]] = []

    for line_number, line in enumerate(raw.splitlines(), start=1):
        for severity, name, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(
                    _finding(
                        "secret_pattern",
                        line=line_number,
                        detail=name,
                        severity=severity,
                    )
                )

    prompt_count = 0
    if prompts_path:
        for prompt in parse_prompt_suite(prompts_path):
            prompt_count += 1
            exact_offset = raw.find(prompt.prompt)
            if exact_offset >= 0:
                findings.append(
                    _finding(
                        "raw_prompt",
                        line=_line_number(raw, exact_offset),
                        detail=f"exact prompt stored: {prompt.label}",
                    )
                )
                continue
            seen_fragments: set[str] = set()
            for fragment in prompt.prompt.splitlines():
                fragment = fragment.strip()
                if len(fragment) < 24 or fragment in seen_fragments:
                    continue
                seen_fragments.add(fragment)
                offset = raw.find(fragment)
                if offset >= 0:
                    findings.append(
                        _finding(
                            "raw_prompt_fragment",
                            line=_line_number(raw, offset),
                            detail=f"prompt fragment stored: {prompt.label}",
                        )
                    )

    parse_errors = 0
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            findings.append(
                _finding(
                    "jsonl_parse_error",
                    line=line_number,
                    detail="line is not valid JSON",
                    severity="error",
                )
            )

    # "info" findings (auth-related VOCABULARY: field/header names, a benign "oauth" method
    # marker) do not fail the audit on their own — only "secret" (credential-shaped value) or
    # "error" (malformed store) findings do. This is what fixes both directions found in
    # review: a legitimate "Authorization failed" error message no longer fails the audit, and
    # an actual Azure/AWS/Google key shape now DOES.
    passed = not any(finding["severity"] != "info" for finding in findings)

    return {
        "store": str(path),
        "store_exists": path.exists(),
        "store_bytes": len(raw.encode("utf-8")),
        "prompts": prompts_path,
        "prompt_count": prompt_count,
        "jsonl_parse_errors": parse_errors,
        "findings": findings,
        "passed": passed,
    }


def render_privacy_audit(result: dict[str, Any]) -> str:
    """Render a compact privacy-audit result."""
    lines = [
        "Privacy audit",
        f"store: {result['store']}",
        f"store exists: {result['store_exists']}",
        f"store bytes: {result['store_bytes']}",
        f"prompts checked: {result['prompt_count']}",
        f"jsonl parse errors: {result['jsonl_parse_errors']}",
        f"findings: {len(result['findings'])}",
    ]
    if result["findings"]:
        lines.append("details:")
        for finding in result["findings"]:
            line = finding.get("line")
            where = f" line={line}" if line is not None else ""
            severity = finding.get("severity", "secret")
            lines.append(f"  - [{severity}] kind={finding['kind']}{where} detail={finding['detail']}")
    lines.append(f"status: {'pass' if result['passed'] else 'fail'}")
    return "\n".join(lines)
