"""Scale chantier S4 — wiring aggregate()/stats onto the incremental projection index.

Run: python tests/test_projection_index_wiring.py

The live surfaces must be BYTE-IDENTICAL whether they read through the incremental index or
the full-scan path, and must silently fall back to the full scan if the index is unusable
(corrupt sidecar, disabled by flag). The index is an acceleration cache, never a source of
truth — turning it on or off must never change a single reported number.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.effective_events import iter_effective_events  # noqa: E402
from tracker.derive.projection_index import effective_events_for_store  # noqa: E402
from tracker.export.live_dashboard import aggregate  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
_FLAG = "TRACKER_DISABLE_PROJECTION_INDEX"


def check(cond: bool, msg: str) -> None:
    global _failures
    if cond:
        print(f"[PASS] {msg}")
    else:
        _failures += 1
        print(f"[FAIL] {msg}")


def _q(tt, q, p, s, a):
    return TokenQuantity(tt, q, p, s, a)


def plain(i, authoritative=True):
    return TokenEvent(
        event_id=f"evt-{i}",
        request_correlation_id=f"rc-{i}",
        trace_id=f"t-{i}",
        span_id=f"s-{i}",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[
            _q(TokenType.INPUT, 11, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
            _q(TokenType.OUTPUT, 7, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING),
        ],
        provider_total_tokens=18,
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": authoritative, "status": "complete", "service_name": f"svc-{i % 3}"},
    )


def partial(rc):
    return TokenEvent(
        event_id=f"evt-partial-{rc}",
        request_correlation_id=rc,
        trace_id="t-x",
        span_id="s-x",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[_q(TokenType.OUTPUT, 40, PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER, Additivity.TOTAL_CONTRIBUTING)],
        data_quality_flags=["partial_stream_estimate", "stream_interrupted"],
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": True, "status": "incomplete", "service_name": "svc-x"},
    )


def final(rc):
    return TokenEvent(
        event_id=f"evt-final-{rc}",
        request_correlation_id=rc,
        trace_id="t-x",
        span_id="s-x",
        provider="openai",
        model="gpt-x",
        api_surface="chat_completions",
        quantities=[_q(TokenType.OUTPUT, 205, PrecisionLevel.EXACT, UsageSource.PROVIDER_STREAM_FINAL, Additivity.TOTAL_CONTRIBUTING)],
        provider_total_tokens=205,
        timestamp="2026-01-01T00:00:00Z",
        observation={"authoritative": True, "status": "complete", "service_name": "svc-x"},
    )


def eff_fingerprint(events):
    out = {}
    for e in events:
        out[e.event_id] = (bool(e.superseded), e.superseded_by, e.event_contributing_tokens, tuple(sorted(e.data_quality_flags)))
    return out


def agg(store):
    result = aggregate(store, window="all")
    result.pop("generated_at", None)  # wall-clock, differs per call
    return result


os.environ.pop(_FLAG, None)

with tempfile.TemporaryDirectory(prefix="tt-wire-") as tmp:
    store = os.path.join(tmp, "events.jsonl")
    repo = FileRepository(store)

    # Batch 1: mixed events + a partial that a later batch will supersede.
    repo.append_many([plain(1), plain(2), plain(3, authoritative=False), partial("stream")])

    # --- helper equivalence (this is what api/main._stats and _scan_summary consume) ---
    full = eff_fingerprint(iter_effective_events(FileRepository(store).iter_events()))
    via_index = eff_fingerprint(effective_events_for_store(store))
    check(via_index == full, "effective_events_for_store == full-scan (batch 1)")

    # --- aggregate() equivalence: index ON vs index DISABLED ---
    a_index = agg(store)
    os.environ[_FLAG] = "1"
    a_disabled = agg(store)
    os.environ.pop(_FLAG, None)
    check(a_index == a_disabled, "aggregate() identical with index ON vs DISABLED (batch 1)")

    # Batch 2: the final retroactively supersedes the batch-1 partial.
    repo.append_many([final("stream"), plain(4)])
    full2 = eff_fingerprint(iter_effective_events(FileRepository(store).iter_events()))
    via_index2 = eff_fingerprint(effective_events_for_store(store))
    check(via_index2 == full2, "effective_events_for_store == full-scan after supersession append")
    check(via_index2.get("evt-partial-stream", (None,))[0] is True, "wired helper reflects the retroactive supersession")

    a_index2 = agg(store)
    os.environ[_FLAG] = "1"
    a_disabled2 = agg(store)
    os.environ.pop(_FLAG, None)
    check(a_index2 == a_disabled2, "aggregate() identical ON vs DISABLED after supersession append")
    check(a_index2["total_tokens"] == a_disabled2["total_tokens"], "total_tokens matches across paths")

    # --- corruption fallback: a garbage sidecar must silently fall back to the full scan ---
    sidecar = f"{os.path.abspath(store)}.projection.sqlite3"
    with open(sidecar, "wb") as handle:
        handle.write(b"this is not a sqlite database at all \x00\x01\x02")
    via_corrupt = eff_fingerprint(effective_events_for_store(store))
    check(via_corrupt == full2, "corrupt sidecar -> helper falls back to full-scan (still correct)")
    a_corrupt = agg(store)
    check(a_corrupt == a_disabled2, "corrupt sidecar -> aggregate() still identical to full-scan")

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
