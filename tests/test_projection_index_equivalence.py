"""Scale chantier S0 — the permanent falsifier for the incremental projection index.

Run: python tests/test_projection_index_equivalence.py

The incremental, persisted ProjectionIndex must produce EXACTLY the same effective state as a
full re-scan through iter_effective_events — always. The hard case is supersession across
refreshes: a partial stream estimate is projected in an early refresh(), then a final usage
event arrives in a LATER batch and must retroactively supersede that already-projected partial.
If the incremental result ever diverges from the full-scan result, the index is wrong.

This is red until tracker/derive/projection_index.py exists (S1-S3 implement it).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.effective_events import iter_effective_events  # noqa: E402
from tracker.derive.projection_index import ProjectionIndex  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def _q(token_type, quantity, precision, source, additivity):
    return TokenQuantity(token_type, quantity, precision, source, additivity)


def plain(i, *, rcid=None, authoritative=True):
    rcid = rcid or f"rcid-plain-{i}"
    return TokenEvent(
        event_id=f"evt-plain-{i}",
        request_correlation_id=rcid,
        trace_id=f"t-{i}",
        span_id=f"s-{i}",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[
            _q(TokenType.INPUT, 10, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
            _q(TokenType.OUTPUT, 5, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
        ],
        provider_total_tokens=15,
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": authoritative, "status": "complete", "service_name": "svc"},
    )


def partial(rcid):
    return TokenEvent(
        event_id=f"evt-partial-{rcid}",
        request_correlation_id=rcid,
        trace_id=f"t-{rcid}",
        span_id=f"s-{rcid}",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[
            _q(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER, Additivity.TOTAL_CONTRIBUTING),
        ],
        data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": True, "status": "incomplete", "service_name": "svc"},
    )


def final(rcid, event_id=None, output=210):
    return TokenEvent(
        event_id=event_id or f"evt-final-{rcid}",
        request_correlation_id=rcid,
        trace_id=f"t-{rcid}",
        span_id=f"s-{rcid}",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[
            _q(TokenType.OUTPUT, output, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL, Additivity.TOTAL_CONTRIBUTING),
        ],
        provider_total_tokens=output,
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": True, "status": "complete", "service_name": "svc"},
    )


def fingerprint(events):
    """Effective state, keyed by event_id, independent of yield order."""
    out = {}
    for e in events:
        out[e.event_id] = (
            bool(e.superseded),
            e.superseded_by,
            e.event_contributing_tokens,
            tuple(sorted(e.data_quality_flags)),
        )
    return out


with tempfile.TemporaryDirectory(prefix="tt-projidx-") as tmp:
    store = os.path.join(tmp, "events.jsonl")
    repo = FileRepository(store)
    index = ProjectionIndex(store)

    # Batch 1: plain events + an authority=false event + a partial that will be superseded LATER.
    repo.append_many([plain(1), plain(2), plain(3, authoritative=False), partial("stream-A")])
    index.refresh()
    # After batch 1 the partial is the best estimate available -> not yet superseded.
    after_b1 = fingerprint(index.iter_effective_events())
    check(after_b1.get("evt-partial-stream-A", (None,))[0] is False, "partial is live (not superseded) before its final arrives")

    # Batch 2: the FINAL for stream-A (retroactively supersedes the batch-1 partial) + more plain.
    repo.append_many([final("stream-A"), plain(4)])
    index.refresh()

    # Batch 3: a duplicate-final correlation (two finals, one rcid) + another plain.
    repo.append_many([final("dup-B", event_id="evt-final-dup-B-1", output=100), plain(5)])
    index.refresh()
    repo.append_many([final("dup-B", event_id="evt-final-dup-B-2", output=100)])
    index.refresh()

    incremental = fingerprint(index.iter_effective_events())
    full = fingerprint(iter_effective_events(repo.iter_events()))

    check(incremental == full, "incremental index effective-state == full-scan effective-state (event-for-event)")
    check(
        incremental.get("evt-partial-stream-A", (None,))[0] is True,
        "the batch-1 partial is retroactively superseded after the final arrives in a later refresh",
    )
    check(
        incremental.get("evt-partial-stream-A", (None, None))[1] == "evt-final-stream-A",
        "retroactively-superseded partial points at the right final",
    )

    # rebuild() from scratch must match the incrementally-maintained result exactly.
    index.rebuild()
    rebuilt = fingerprint(index.iter_effective_events())
    check(rebuilt == incremental, "rebuild() from scratch == incrementally-maintained state")
    check(rebuilt == full, "rebuild() == full-scan")

    if incremental != full:
        only_inc = {k: incremental[k] for k in incremental if incremental.get(k) != full.get(k)}
        print("  DIVERGENCE (event_id -> incremental/full):")
        for k, v in list(only_inc.items())[:10]:
            print(f"    {k}: incr={v} full={full.get(k)}")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
