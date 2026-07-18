"""Regression — privacy audit severity split: fewer false positives, no false negatives.

Run: python tests/test_privacy_audit_severity_regression.py

Found during a rigorous review of tracker/proxy/privacy.py:
  - FALSE POSITIVES: legitimate, secret-free operational text ("Authorization failed: invalid
    credentials", "auth_method": "oauth") failed the audit merely for containing auth-related
    VOCABULARY, with no actual secret value present.
  - FALSE NEGATIVES: an Azure key (the exact shape leaked once already in this project's own
    session) and an AWS access key ID were not covered by ANY pattern and passed undetected.
Fixed with a severity split (info = vocabulary mention, does not fail; secret/error = actual
leak or malformed store, does fail) plus 3 new credential-shape patterns.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.proxy.privacy import audit_store  # noqa: E402

_failures = 0


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def audit_payload(payload: dict) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")
        path = f.name
    try:
        return audit_store(path)
    finally:
        os.unlink(path)


# --- false positives: must now PASS (secret-free operational text) ---
r1 = audit_payload({"observation": {"error_message": "Authorization failed: invalid credentials"}})
check(r1["passed"] is True, f"FIXED: a legitimate auth error MESSAGE no longer fails the audit (got passed={r1['passed']})")

r2 = audit_payload({"observation": {"auth_method": "oauth"}})
check(r2["passed"] is True, f"FIXED: 'auth_method: oauth' metadata no longer fails the audit (got passed={r2['passed']})")

# these are still SURFACED as info-severity findings (not silently dropped), just don't fail
check(any(f["severity"] == "info" for f in r1["findings"]), "the auth-vocabulary mention is still visible as an info-severity finding")

# --- false negatives: must now FAIL (real credential shapes) ---
# A SYNTHETIC 88-char alphanumeric run in the Azure-key shape (matches [A-Za-z0-9]{80,100}).
# Deliberately fake/recognizable — never paste a real key into a test fixture; it would be
# committed into git history permanently.
azure_key = "FAKEZAZURE" + "0123456789 abcdefgh".replace(" ", "") + "FAKEZAZURE" * 5 + "PADPADPAD00"
r3 = audit_payload({"debug_note": f"captured with key {azure_key}"})
check(r3["passed"] is False, f"FIXED: an Azure-key-shaped secret is now detected (got passed={r3['passed']})")
check(
    any(f["detail"] == "azure_key_shaped" and f["severity"] == "secret" for f in r3["findings"]),
    "azure_key_shaped finding has secret severity",
)

fake_aws_access_key_id = "AKIA" + "IOSFODNN7EXAMPLE"
r4 = audit_payload({"debug_note": fake_aws_access_key_id})
check(r4["passed"] is False, f"FIXED: an AWS access key ID is now detected (got passed={r4['passed']})")
check(any(f["detail"] == "aws_access_key_id" for f in r4["findings"]), "aws_access_key_id finding is present")

# --- a genuine secret must still fail (unchanged behavior) ---
fake_bearer_token = "Bearer " + "abc123." + "def456~ghi789"
r5 = audit_payload({"authorization": fake_bearer_token})
check(r5["passed"] is False, "a real bearer-token-shaped value still fails the audit")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
