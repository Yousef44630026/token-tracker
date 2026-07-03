"""Track REAL Claude Code token usage — from local session transcripts, no API credit.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" examples\\track_claude_code_usage.py

Claude Code writes one JSONL transcript per session under ~/.claude/projects/<project>/*.jsonl.
Each assistant turn's message.usage is the exact Anthropic Messages usage object. A single turn
is split across multiple JSONL lines (one per content block), all repeating the SAME usage
under the same requestId — the importer de-duplicates by requestId so tokens are counted
exactly once per turn. Raw prompts/responses are never read, only token facts.
"""

import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker.models.enums import TokenType  # noqa: E402
from tracker.proxy.claude_code_logs import default_claude_home, import_new_claude_code_events  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

print(f"Claude home : {default_claude_home()}")
events = import_new_claude_code_events()
print(f"Real assistant-turn events found (de-duplicated by requestId) : {len(events)}\n")

if not events:
    print("No Claude Code session logs found (~/.claude/projects). Use Claude Code, then re-run.")
    sys.exit(0)

total = sum(e.event_contributing_tokens for e in events)
inp = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.INPUT)
out = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.OUTPUT)
cache_read = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.CACHED_INPUT)
cache_write = sum((q.quantity or 0) for e in events for q in e.quantities if q.token_type == TokenType.CACHE_CREATION_INPUT)
sidechain = sum(1 for e in events if e.observation and e.observation.get("is_sidechain"))

by_session = defaultdict(lambda: [0, 0])
for e in events:
    by_session[e.trace_id][0] += 1
    by_session[e.trace_id][1] += e.event_contributing_tokens

print("=" * 64)
print("  REAL CLAUDE CODE USAGE (mesure, pas simule)")
print("=" * 64)
print(f"  events (turns)   : {len(events)}   (sous-agents/sidechain: {sidechain})")
print(f"  sessions         : {len(by_session)}")
print(f"  TOTAL tokens      : {total:,}")
print(f"    input (frais)   : {inp:,}")
print(f"    cache_read      : {cache_read:,}  (bucket additif Anthropic, compte pour de vrai)")
print(f"    cache_creation  : {cache_write:,}  (bucket additif Anthropic, compte pour de vrai)")
print(f"    output          : {out:,}")
print("  " + "-" * 60)
print("  top sessions par tokens :")
for sid, (cnt, tok) in sorted(by_session.items(), key=lambda kv: kv[1][1], reverse=True)[:5]:
    print(f"    {sid[:20]:<22} {cnt:>4} turns  {tok:>12,} tokens")
print("=" * 64)

out_dir = os.path.join(ROOT, "demo_output")
os.makedirs(out_dir, exist_ok=True)
store = os.path.join(out_dir, "claude_code_real_usage.jsonl")
if os.path.exists(store):
    os.remove(store)
FileRepository(store).append_many(events)
print(f"\nVrai dataset persiste : {store}")
