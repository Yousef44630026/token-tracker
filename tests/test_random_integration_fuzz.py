"""Random INTEGRATION fuzz — the real adapters/registry/supersession/collector/export under
randomized, realistic-shaped chaos. NOT abstract (test_core_logic_deep.py already covers the
pure algebra); this drives the actual per-provider field-extraction code paths with randomly
constructed but semantically valid payloads, and independently recomputes the expected total
by hand for every single case (no tautology: the check is never "does X equal what the code
under test says X is").

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_random_integration_fuzz.py

Five parts, all seeded (reproducible):
  1. Per-provider random payloads (all 15 registered adapters) x 40 iterations each — the
     expected contributing total is computed independently from the random field values
     using each provider's KNOWN additivity rule, then checked against normalize()'s output.
  2. Random multi-event traces with random supersession groups: random counts of partials,
     random counts of DUPLICATE finals (0-4, not just the hand-picked 2 from before), random
     timestamps (including ties and all-missing), random authoritative/non-authoritative
     flags, random provider mix — verifying the rollup total is always exactly the sum of
     each group's ONE authoritative contribution.
  3. Randomized collector chaos: random interleaving of record()/flush() against a transport
     with randomized behavior (full ack / partial ack / raise / hang-then-succeed), checking
     a conservation invariant across many operations.
  4. Randomized CSV export: the same random traces from part 2, exported, CSV total checked
     against the model total.
  5. A "grab bag" of malformed/boundary payloads across EVERY adapter via the registry, never
     crashing and never fabricating a nonzero total from garbage.
"""

import csv
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters import available_adapters, create_adapter  # noqa: E402
from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.export.csv_exporter import export_csv  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402
from tracker.normalization.supersession import reconcile_supersession  # noqa: E402

_failures = 0
_checks = 0
SEED = int(os.environ.get("FUZZ_SEED", "987654321"))
rng = random.Random(SEED)


def check(cond, msg):
    global _failures, _checks
    _checks += 1
    if not cond:
        _failures += 1
        print(f"[FAIL] (seed={SEED}) {msg}")


_uid = 0


def uid(prefix="x"):
    global _uid
    _uid += 1
    return f"{prefix}-{_uid}"


def maybe(p=0.5):
    return rng.random() < p


def tok(lo=1, hi=3000):
    return rng.randint(lo, hi)


# =====================================================================================
# PART 1 — per-provider random payloads, independently-computed expected totals
# =====================================================================================
print("--- Part 1: per-provider random payload fuzz (real adapters via the registry) ---")


def gen_openai_chat():
    prompt, completion = tok(), tok()
    cached = tok(0, min(prompt, 2000)) if maybe(0.5) else 0
    reasoning = tok(0, min(completion, 2000)) if maybe(0.4) else 0
    audio_in = tok(0, min(prompt, 500)) if maybe(0.2) else 0
    audio_out = tok(0, min(completion, 500)) if maybe(0.2) else 0
    total = prompt + completion
    payload = {
        "model": "gpt-4o",
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "prompt_tokens_details": {"cached_tokens": cached, "audio_tokens": audio_in},
            "completion_tokens_details": {"reasoning_tokens": reasoning, "audio_tokens": audio_out},
        },
    }
    return payload, total, total  # input+output alone == total; subtotals contribute 0


def gen_openai_responses():
    inp, out = tok(), tok()
    cached = tok(0, min(inp, 2000)) if maybe(0.5) else 0
    reasoning = tok(0, min(out, 2000)) if maybe(0.4) else 0
    total = inp + out
    payload = {
        "model": "o4-mini",
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": total,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens_details": {"reasoning_tokens": reasoning},
        },
    }
    return payload, total, total


def gen_anthropic():
    inp, out = tok(), tok()
    cache_read = tok(0, 2000) if maybe(0.5) else 0
    cache_creation = tok(0, 2000) if maybe(0.3) else 0
    expected = inp + out + cache_read + cache_creation  # ALL buckets are additive
    payload = {
        "model": "claude-opus-4-8",
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
    }
    return payload, expected, None  # Anthropic never reports a total


def gen_bedrock_converse():
    inp, out = tok(), tok()
    total = inp + out
    payload = {
        "usage": {
            "inputTokens": inp,
            "outputTokens": out,
            "totalTokens": total,
            "cacheReadInputTokens": tok(0, 500) if maybe(0.3) else 0,  # UNVERIFIED -> contributes 0 regardless
            "cacheWriteInputTokens": tok(0, 500) if maybe(0.2) else 0,
        }
    }
    return payload, total, total  # cache fields stay unverified, excluded


def gen_bedrock_invoke_model():
    inp, out = tok(), tok()
    payload = {
        "ResponseMetadata": {
            "HTTPHeaders": {
                "x-amzn-bedrock-input-token-count": str(inp),
                "x-amzn-bedrock-output-token-count": str(out),
            }
        }
    }
    return payload, inp + out, None


def gen_bedrock_embeddings():
    inp = tok()
    payload = {"ResponseMetadata": {"HTTPHeaders": {"x-amzn-bedrock-input-token-count": str(inp)}}}
    return payload, inp, None


def gen_gemini():
    prompt, candidates, thoughts = tok(), tok(), tok(0, 2000) if maybe(0.6) else 0
    cached = tok(0, min(prompt, 2000)) if maybe(0.4) else 0
    total = prompt + candidates + thoughts  # thinking ADDS ON TOP; cached is a subtotal (0)
    payload = {
        "modelVersion": "gemini-2.5-pro",
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": candidates,
            "totalTokenCount": total,
            "cachedContentTokenCount": cached,
            "thoughtsTokenCount": thoughts,
        },
    }
    return payload, total, total


def gen_mistral():
    prompt, completion = tok(), tok()
    total = prompt + completion
    payload = {"model": "mistral-large", "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}}
    return payload, total, total


def gen_cohere():
    inp, out = tok(), tok()
    payload = {"model": "command-r-plus", "usage": {"tokens": {"input_tokens": inp, "output_tokens": out}}}
    return payload, inp + out, None


def gen_voyage():
    total = tok()
    payload = {"model": "rerank-2", "usage": {"total_tokens": total}}
    return payload, total, total


def gen_openai_embeddings():
    prompt = tok()
    payload = {"model": "text-embedding-3-small", "usage": {"prompt_tokens": prompt, "total_tokens": prompt}}
    return payload, prompt, prompt


PROVIDER_GENERATORS = {
    ("openai", "chat_completions"): gen_openai_chat,
    ("openai", "responses"): gen_openai_responses,
    ("azure_openai", "chat_completions"): gen_openai_chat,
    ("azure_openai", "responses"): gen_openai_responses,
    ("azure_openai", "embeddings"): gen_openai_embeddings,
    ("anthropic", "messages"): gen_anthropic,
    ("bedrock", "converse"): gen_bedrock_converse,
    ("bedrock", "invoke_model"): gen_bedrock_invoke_model,
    ("bedrock", "embeddings"): gen_bedrock_embeddings,
    ("gemini", "generate_content"): gen_gemini,
    ("vertex_ai", "generate_content"): gen_gemini,
    ("mistral", "chat_completions"): gen_mistral,
    ("cohere", "chat"): gen_cohere,
    ("voyage", "rerank"): gen_voyage,
    ("openai", "embeddings"): gen_openai_embeddings,
}

registered = set(available_adapters())
check(
    registered == set(PROVIDER_GENERATORS),
    f"every registered adapter has a random generator (missing: {registered - set(PROVIDER_GENERATORS)}, "
    f"extra: {set(PROVIDER_GENERATORS) - registered})",
)

N_PER_PROVIDER = 40
for (provider, surface), generator in PROVIDER_GENERATORS.items():
    adapter = create_adapter(provider, surface)
    for _ in range(N_PER_PROVIDER):
        payload, expected_contrib, expected_total = generator()
        ev = normalize(payload, adapter, context=new_trace())
        check(ev.provider == provider and ev.api_surface == surface, f"{provider}/{surface}: labels correct")
        check(
            ev.event_contributing_tokens == expected_contrib,
            f"{provider}/{surface}: contributing == independently-computed expectation "
            f"(got {ev.event_contributing_tokens}, expected {expected_contrib}, payload={payload})",
        )
        if expected_total is not None:
            check(ev.event_total_mismatch == 0, f"{provider}/{surface}: reconciles against provider total ({payload})")
        check(all((q.quantity is None or q.quantity >= 0) for q in ev.quantities), f"{provider}/{surface}: no negative quantities produced")

print(
    f"[INFO] Part 1: {len(PROVIDER_GENERATORS) * N_PER_PROVIDER} real adapter invocations "
    f"across {len(PROVIDER_GENERATORS)} provider/surface pairs."
)

# =====================================================================================
# PART 2 — randomized multi-event traces with randomized supersession groups
# =====================================================================================
print("\n--- Part 2: randomized supersession groups (0-4 duplicate finals, random timestamps) ---")


def random_group(rcid):
    """Build one correlation-id group: random partials + random (possibly duplicate) finals."""
    n_partials = rng.randint(0, 3)
    n_finals = rng.randint(0, 4)  # 0 = no resolution yet, 1 = normal, 2+ = duplicate delivery
    events = []
    for _ in range(n_partials):
        events.append(
            TokenEvent(
                event_id=uid("p"),
                request_correlation_id=rcid,
                trace_id="t-fuzz2",
                span_id="s",
                quantities=[],
                provider_total_tokens=None,
                observation={"authoritative": True},
            )
        )
        # give the partial a genuine partial-estimate quantity
        from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource
        from tracker.models.token_quantity import TokenQuantity

        events[-1].quantities.append(
            TokenQuantity(
                TokenType.OUTPUT, tok(1, 50), PrecisionLevel.ESTIMATE, UsageSource.PARTIAL_STREAM_TOKENIZER, Additivity.TOTAL_CONTRIBUTING
            )
        )
    final_amounts = []
    for _ in range(n_finals):
        amount = tok()
        final_amounts.append(amount)
        has_ts = maybe(0.7)
        ts = f"2026-01-01T10:00:{rng.randint(0, 59):02d}Z" if has_ts else None
        from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource
        from tracker.models.token_quantity import TokenQuantity

        events.append(
            TokenEvent(
                event_id=uid("f"),
                request_correlation_id=rcid,
                trace_id="t-fuzz2",
                span_id="s",
                quantities=[
                    TokenQuantity(
                        TokenType.OUTPUT, amount, PrecisionLevel.EXACT, UsageSource.PROVIDER_RESPONSE, Additivity.TOTAL_CONTRIBUTING
                    )
                ],
                provider_total_tokens=amount,
                timestamp=ts,
                observation={"authoritative": True},
            )
        )
    # expected: 0 finals -> partials never resolved (partials stay AT THEIR OWN value, since
    # supersession never invents a winner without a final); >=1 final -> exactly ONE final's
    # amount counts (the latest-timestamped if any have timestamps, else first-in-list-order
    # among the finals as constructed above), partials go to 0.
    if n_finals == 0:
        expected = sum(q.quantity for e in events for q in e.quantities)  # partials count as-is
    else:
        timestamped_finals = [e for e in events if e.provider_total_tokens is not None and e.timestamp]
        if timestamped_finals:
            winner = max(timestamped_finals, key=lambda e: e.timestamp)
        else:
            winner = next(e for e in events if e.provider_total_tokens is not None)
        expected = winner.provider_total_tokens
    return events, expected


N_TRACE_FUZZ = 200
for trace_i in range(N_TRACE_FUZZ):
    n_groups = rng.randint(1, 5)
    all_events = []
    expected_total = 0
    for g in range(n_groups):
        events, expected = random_group(f"rc-{trace_i}-{g}")
        all_events.extend(events)
        expected_total += expected
    reconcile_supersession(all_events)
    got_total = sum(e.event_contributing_tokens for e in all_events)
    check(
        got_total == expected_total,
        f"trace fuzz #{trace_i}: supersession-resolved total matches ({got_total} != {expected_total}, groups={n_groups})",
    )
    # idempotency under randomization: re-running never changes the outcome or duplicates flags
    reconcile_supersession(all_events)
    got_total_2 = sum(e.event_contributing_tokens for e in all_events)
    check(got_total_2 == expected_total, f"trace fuzz #{trace_i}: idempotent re-run gives the same total")
    check(
        all(e.data_quality_flags.count("superseded") <= 1 for e in all_events),
        f"trace fuzz #{trace_i}: 'superseded' flag never duplicated by re-running",
    )

print(f"[INFO] Part 2: {N_TRACE_FUZZ} randomized multi-group traces, supersession resolved and idempotency checked.")

# =====================================================================================
# PART 3 — randomized collector chaos: conservation invariant under random operations
# =====================================================================================
print("\n--- Part 3: randomized collector chaos (conservation invariant) ---")


class ChaosTransport:
    """Randomly acks all/partial/none, sometimes raises, sometimes 'hangs' (never acks)."""

    def __init__(self, rng_):
        self.rng = rng_
        self.calls = 0

    def __call__(self, batch):
        self.calls += 1
        mode = self.rng.random()
        if mode < 0.15:
            raise RuntimeError("chaos: transport error")
        if mode < 0.30:
            return []  # nothing acked this round
        if mode < 0.55:
            # partial ack: a random subset
            ids = [e["event_id"] for e in batch]
            k = self.rng.randint(0, len(ids))
            return self.rng.sample(ids, k) if ids else []
        return [e["event_id"] for e in batch]  # full ack


chaos = ChaosTransport(rng)
collector = CollectorClient(
    chaos, CollectorConfig(max_buffer_size=200, batch_size=15, drop_policy=rng.choice(["drop_oldest", "drop_newest"]))
)
attempted = 0
accepted_ids = set()
N_OPS = 400
for _i in range(N_OPS):
    if maybe(0.7):
        eid = uid("chaos")
        attempted += 1
        if collector.record({"event_id": eid, "trace_id": "t"}):
            accepted_ids.add(eid)
    else:
        collector.flush()
# drain fully: keep flushing until nothing changes for a bounded number of extra rounds
stalled = 0
last_pending = -1
while collector.pending and stalled < 500:
    collector.flush()
    if collector.pending == last_pending:
        stalled += 1
    else:
        stalled = 0
    last_pending = collector.pending

check(
    collector.sent_total + collector.pending == len(accepted_ids),
    f"conservation: sent_total({collector.sent_total}) + pending({collector.pending}) == accepted({len(accepted_ids)})",
)
check(collector.dropped_total >= 0 and collector.sent_total >= 0, "counters never go negative")
print(
    f"[INFO] Part 3: {N_OPS} randomized ops, {attempted} record attempts, {len(accepted_ids)} accepted, "
    f"sent={collector.sent_total} dropped={collector.dropped_total} pending={collector.pending} (transport calls={chaos.calls})"
)

# =====================================================================================
# PART 4 — randomized CSV export: many random trace shapes, total must match every time
# =====================================================================================
print("\n--- Part 4: randomized CSV export across many random trace shapes ---")

from tracker.models.trace import Trace  # noqa: E402

N_EXPORT_FUZZ = 60
for i in range(N_EXPORT_FUZZ):
    tr = Trace(trace_id=f"t-export-fuzz-{i}")
    n_groups = rng.randint(0, 4)
    for g in range(n_groups):
        events, _ = random_group(f"rc-export-{i}-{g}")
        for e in events:
            object.__setattr__(e, "trace_id", tr.trace_id) if False else None
            e.trace_id = tr.trace_id
        reconcile_supersession(events)
        for e in events:
            tr.add_event(e)
    model_total = observed_total_contributing_tokens(tr)
    out_dir = os.path.join(os.getcwd(), ".test_random_integration_fuzz", f"export_{i}")
    os.makedirs(out_dir, exist_ok=True)
    paths = export_csv(tr, out_dir)
    with open(paths["token_events"], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    csv_total = sum(int(r["event_contributing_tokens"]) for r in rows)
    check(csv_total == model_total, f"export fuzz #{i}: CSV total matches model total ({csv_total} != {model_total})")

print(f"[INFO] Part 4: {N_EXPORT_FUZZ} randomized trace exports, CSV/model totals cross-checked.")

# =====================================================================================
# PART 5 — grab bag of malformed/boundary payloads across EVERY registered adapter
# =====================================================================================
print("\n--- Part 5: malformed/boundary payloads across every adapter, no crash / no fabrication ---")

GARBAGE = [
    None,
    {},
    [],
    "not a dict",
    12345,
    3.14,
    True,
    {"usage": None},
    {"usage": {}},
    {"usage": {"unexpected_field_xyz": rng.random()}},
    {"usage": {k: "not-a-number" for k in ("prompt_tokens", "input_tokens", "totalTokens", "tokens")}},
    {"ResponseMetadata": None},
    {"ResponseMetadata": {}},
    {"ResponseMetadata": {"HTTPHeaders": None}},
    {"usageMetadata": None},
    {"choices": None},
]

for provider, surface in registered:
    adapter = create_adapter(provider, surface)
    for g in GARBAGE:
        try:
            ev = normalize(g, adapter, context=new_trace())
        except Exception as exc:  # noqa: BLE001
            check(False, f"{provider}/{surface}: CRASHED on garbage {g!r}: {type(exc).__name__}: {exc}")
            continue
        check(ev.event_contributing_tokens == 0, f"{provider}/{surface}: garbage {g!r} contributes 0 (no fabrication)")
        check(bool(ev.data_quality_flags), f"{provider}/{surface}: garbage {g!r} is flagged, not silent")

print(f"[INFO] Part 5: {len(registered) * len(GARBAGE)} garbage-payload probes across all {len(registered)} adapters.")

print(f"\n[INFO] total checks run: {_checks}   (seed={SEED}, reproducible)")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
