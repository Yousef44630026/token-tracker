"""Ledger-level (session_id, request_id) dedup — the import survives id-scheme history.

The import event_id was originally derived from the transcript's ABSOLUTE PATH, so ~3,700
historical ledger events carry path-dependent ids. The id scheme is now stable
(session_id + request_id), but the collector dedups by event_id only: any rewind that
re-reads pre-fix bytes (project-directory rename, checkpoint loss, machine migration —
exactly the moves the new scheme is meant to survive) would re-import the whole pre-fix
history under NEW ids, silently doubling the ledger.

The guard: before posting, the importer loads the (session_id, request_id) pairs already
present in the ledger (archive-aware — rotated segments count) and drops any turn already
recorded, REGARDLESS of which id scheme stored it. Import becomes idempotent across id
history, renames, and checkpoint resets.

Run: python tests/test_claude_import_ledger_dedup.py
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import import_claude_to_collector as importer  # noqa: E402
from tests._harness import make_checker  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
root = Path(__file__).resolve().parent.parent
owned_temp = "TRACKER_TEST_WORKSPACE" not in os.environ
work = Path(os.environ.get("TRACKER_TEST_WORKSPACE") or root / f".test_claude_import_ledger_dedup_{uuid.uuid4().hex}")
work.mkdir(parents=True, exist_ok=True)
store = work / "collector.jsonl"


def ledger_event(event_id: str, session_id: str, request_id: str) -> TokenEvent:
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=event_id,
        trace_id=session_id,
        span_id=f"claude-code-{request_id}",
        provider="anthropic",
        model="claude-test",
        api_surface="messages",
        quantities=[
            TokenQuantity(TokenType.INPUT, 10, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
        ],
        observation={
            "authoritative": True,
            "status": "complete",
            "source": "claude_code_session_log",
            "session_id": session_id,
            "request_id": request_id,
        },
    )


def imported_event(session_id: str, request_id: str) -> TokenEvent:
    # What the importer would build TODAY (new stable id scheme) for the same turn.
    return ledger_event(f"claude-code-new-{uuid.uuid4().hex[:12]}", session_id, request_id)


try:
    # --- ledger: one ACTIVE pre-fix event (old-style id) + one ARCHIVED pre-fix event ---
    repo = FileRepository(str(store))
    repo.append(ledger_event("claude-code-OLDPATHSTYLE0001", "sess-1", "req-active"))
    archive_dir = Path(f"{store}.archive")
    archive_dir.mkdir()
    archived = ledger_event("claude-code-OLDPATHSTYLE0002", "sess-1", "req-archived")
    with gzip.open(archive_dir / "segment-0001.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(archived.to_dict(), ensure_ascii=False) + "\n")

    # --- candidate imports: the two known turns (under NEW ids) + one genuinely new turn ---
    candidates = [
        imported_event("sess-1", "req-active"),
        imported_event("sess-1", "req-archived"),
        imported_event("sess-1", "req-genuinely-new"),
    ]

    kept, skipped = importer.filter_preexisting_turns(candidates, store)
    check(skipped == 2, f"the two pre-fix turns are skipped despite different event_ids (skipped={skipped})")
    check(
        [e.observation.get("request_id") for e in kept] == ["req-genuinely-new"],
        "only the genuinely new turn survives the ledger dedup",
    )
    check(
        any(e.observation.get("request_id") == "req-archived" for e in candidates),
        "sanity: the archived turn WAS a candidate (dedup is archive-aware, not luck)",
    )

    # --- rename/rewind equivalence: running the same filter twice stays stable ---
    kept2, skipped2 = importer.filter_preexisting_turns(candidates, store)
    check((len(kept2), skipped2) == (1, 2), "re-running the filter (rewind simulation) is idempotent")

    # --- missing store: filter must be a no-op, never a crash ---
    kept3, skipped3 = importer.filter_preexisting_turns(candidates, work / "absent.jsonl")
    check(len(kept3) == 3 and skipped3 == 0, "missing ledger -> no filtering, no crash")

    # --- an event without session/request observation is never wrongly dropped ---
    anonymous = ledger_event("claude-code-anon", "sess-anon", "req-anon")
    anonymous.observation.pop("session_id", None)
    anonymous.observation.pop("request_id", None)
    kept4, skipped4 = importer.filter_preexisting_turns([anonymous], store)
    check(len(kept4) == 1 and skipped4 == 0, "an event lacking (session, request) keys is kept, not silently dropped")

finally:
    if owned_temp:
        shutil.rmtree(work, ignore_errors=True)

raise SystemExit(check.report("RESULT test_claude_import_ledger_dedup"))
