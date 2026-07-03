"""Track REAL Codex CLI token usage — from local session logs, no API credit, no new calls.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\track_codex_usage.py

Codex writes token_count events into ~/.codex/sessions/*.jsonl. This reads them through the
real tracker pipeline (OpenAI Responses adapter), aggregates true usage, and persists the
events to a JSONL you can inspect. Raw prompts/responses are never read — only token facts.
"""

import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.models.enums import TokenType  # noqa: E402
from tracker.proxy.codex_logs import default_codex_home, import_new_codex_events  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

print(f"Codex home : {default_codex_home()}")
events = import_new_codex_events()
print(f"Real token_count events found : {len(events)}\n")

if not events:
    print("No Codex session logs found (~/.codex/sessions). Run Codex, then re-run this script.")
    sys.exit(0)

total = sum(e.event_contributing_tokens for e in events)
inp = sum(q.quantity_in_total for e in events for q in e.quantities if q.token_type == TokenType.INPUT)
out = sum(q.quantity_in_total for e in events for q in e.quantities if q.token_type == TokenType.OUTPUT)
cached = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.CACHED_INPUT)
reasoning = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.REASONING)

by_session = defaultdict(lambda: [0, 0])
for e in events:
    by_session[e.trace_id][0] += 1
    by_session[e.trace_id][1] += e.event_contributing_tokens

print("=" * 60)
print("  REAL CODEX USAGE (mesuré, pas simulé)")
print("=" * 60)
print(f"  events        : {len(events)}")
print(f"  sessions      : {len(by_session)}")
print(f"  TOTAL tokens  : {total:,}")
print(f"    input       : {inp:,}")
print(f"    output      : {out:,}")
print(f"    cached      : {cached:,}  (sous-total de l'input, contribue 0)")
print(f"    reasoning   : {reasoning:,}  (sous-total de l'output, contribue 0)")
print("  " + "-" * 56)
print("  top sessions par tokens :")
for sid, (cnt, tok) in sorted(by_session.items(), key=lambda kv: kv[1][1], reverse=True)[:5]:
    print(f"    {sid[:20]:<22} {cnt:>4} events  {tok:>10,} tokens")
print("=" * 60)

# persist the real events (JSONL — no trace_id constraint at the event grain)
out_dir = os.path.join(ROOT, "demo_output")
os.makedirs(out_dir, exist_ok=True)
store = os.path.join(out_dir, "codex_real_usage.jsonl")
if os.path.exists(store):
    os.remove(store)
FileRepository(store).append_many(events)
print(f"\nVrai dataset persiste : {store}")
