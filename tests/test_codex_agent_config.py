"""Project custom agents must stay pinned to the intended flagship model."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
config = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8"))
agent_dir = root / ".codex" / "agents"
expected = {
    "analytics_export_auditor",
    "core_accounting_auditor",
    "domain_scorecards",
    "ops_release_verifier",
    "provider_surface_auditor",
    "storage_collector_warden",
    "trace_stream_guardian",
}

check(config["features"]["multi_agent"] is True, "project enables Codex multi-agent workflows")
check(config["agents"]["max_threads"] == len(expected), "thread cap fits one complete specialist pass")
check(config["agents"]["max_depth"] == 1, "subagents cannot recursively fan out")

loaded = {}
for path in sorted(agent_dir.glob("*.toml")):
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    loaded[payload["name"]] = payload
    check(payload["model"] == "gpt-5.6-sol", f"{payload['name']} uses gpt-5.6-sol")
    check(payload["model_reasoning_effort"] == "ultra", f"{payload['name']} uses ultra reasoning")
    check(payload["sandbox_mode"] == "read-only", f"{payload['name']} is audit-only by default")
    check(bool(payload["description"].strip()), f"{payload['name']} has routing guidance")
    check(bool(payload["developer_instructions"].strip()), f"{payload['name']} has domain instructions")
    playbook = agent_dir / f"{path.stem}.md"
    check(playbook.is_file(), f"{payload['name']} retains its detailed Markdown playbook")

check(set(loaded) == expected, "all and only the seven tracker specialists are configured")

sys.exit(check.report("RESULT test_codex_agent_config"))
