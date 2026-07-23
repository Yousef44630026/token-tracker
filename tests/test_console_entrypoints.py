"""Every declared console surface resolves to an importable callable."""

from __future__ import annotations

import importlib
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
scripts = project["project"]["scripts"]

required = {
    "ai-token-tracker-collector",
    "ai-token-tracker-proxy",
    "ai-token-tracker-doctor",
    "ai-token-tracker-dashboard",
    "ai-token-tracker-live-dashboard",
    "ai-token-tracker-release-readiness",
    "ai-token-tracker-scale-probe",
    "ai-token-tracker-vertex-smoke",
    "ai-token-tracker-bedrock-stream-smoke",
}
check(required <= set(scripts), "the distributable exposes every operational and reporting surface")

for name, target in scripts.items():
    module_name, callable_name = target.split(":", 1)
    module = importlib.import_module(module_name)
    check(callable(getattr(module, callable_name, None)), f"console entry point {name} resolves to {target}")

sys.exit(check.report("RESULT test_console_entrypoints"))
