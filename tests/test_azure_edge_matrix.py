"""Smart adversarial matrix — probe every Azure surface for a counting bug.

This does not confirm the happy path; it tries to BREAK the pipeline across chat_completions,
responses, and embeddings with the malformed/edge payloads a real Azure fleet eventually emits,
and asserts the universal invariants hold for every one:

  * INV (reconcile): a nonzero mismatch is ALWAYS flagged — never a silent wrong number.
  * INV (no fabrication): zero-valued detail fields never create phantom modality quantities.
  * INV (no double count): cache/reasoning/audio are subtotals that contribute 0; the total is
    input+output even when a subtotal is nonsensically larger than its parent.
  * INV (fail loud, not crash): bad types / dangling subtotals become normalization_error events,
    never an exception into the caller.
  * INV (detectors fire): inconsistent total -> provider_total_mismatch; unknown usage field ->
    provider_schema_drift; missing usage -> raw_usage_missing.
  * INV (unknown != zero): missing provider total -> mismatch is None, never a fabricated 0.

Run: python tests/test_azure_edge_matrix.py
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.adapters import create_adapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

check = make_checker()

CHAT = create_adapter("azure_openai", "chat_completions", deployment="d")
RESP = create_adapter("azure_openai", "responses", deployment="d")
EMB = create_adapter("azure_openai", "embeddings", deployment="d")


def run(response, adapter):
    """Normalize; must NEVER raise into the caller."""
    return normalize(response, adapter, context=new_trace(), observation={"authoritative": True, "status": "complete"})


def assert_universal(label, ev):
    """The invariants every event must satisfy, whatever the payload."""
    m = ev.event_total_mismatch
    flags = ev.data_quality_flags
    qtypes = [q.token_type.value for q in ev.quantities]
    # never a silent wrong number
    if m not in (0, None):
        check(any("mismatch" in f for f in flags), f"{label}: nonzero mismatch ({m}) is flagged, not silent")
    # never a fabricated modality token from a zero/absent detail
    for phantom in ("image_input", "video_input"):
        check(phantom not in qtypes, f"{label}: no fabricated {phantom}")
    # a counted quantity is only input/output/embedding/audio — subtotals contribute 0
    for q in ev.quantities:
        if q.quantity_in_total > 0:
            check(
                q.token_type.value in ("input", "output", "embedding", "rerank_input", "audio_input", "audio_output"),
                f"{label}: only real independent types contribute ({q.token_type.value})",
            )


def chat(**usage):
    return NS(model="gpt-5-mini", usage=NS(**usage))


# ---------------- chat completions: clean cases reconcile to Azure's total ----------------
CLEAN = {
    "cache=0 reasoning=0": (chat(prompt_tokens=100, completion_tokens=20, total_tokens=120,
                                 prompt_tokens_details=NS(cached_tokens=0), completion_tokens_details=NS(reasoning_tokens=0)), 120),
    "cache 100%": (chat(prompt_tokens=1000, completion_tokens=50, total_tokens=1050, prompt_tokens_details=NS(cached_tokens=1000)), 1050),
    "reasoning == full output": (chat(prompt_tokens=10, completion_tokens=500, total_tokens=510, completion_tokens_details=NS(reasoning_tokens=500)), 510),
    "prediction tokens": (chat(prompt_tokens=10, completion_tokens=200, total_tokens=210,
                               completion_tokens_details=NS(reasoning_tokens=64, accepted_prediction_tokens=30, rejected_prediction_tokens=10)), 210),
    "audio in+out": (chat(prompt_tokens=100, completion_tokens=40, total_tokens=140,
                          prompt_tokens_details=NS(audio_tokens=60), completion_tokens_details=NS(audio_tokens=20)), 140),
    "input only": (NS(model="m", usage=NS(prompt_tokens=100, total_tokens=100)), 100),
    "all zero": (chat(prompt_tokens=0, completion_tokens=0, total_tokens=0), 0),
    "very large": (chat(prompt_tokens=10**12, completion_tokens=10**12, total_tokens=2 * 10**12), 2 * 10**12),
}
for label, (resp, expected) in CLEAN.items():
    ev = run(resp, CHAT)
    assert_universal(label, ev)
    check(ev.event_total_mismatch == 0, f"chat clean [{label}]: reconciles to Azure total (mismatch 0)")
    check(ev.event_contributing_tokens == expected, f"chat clean [{label}]: contributing == {expected}")
    check(not ev.data_quality_flags, f"chat clean [{label}]: no false data-quality flag")

# subtotal larger than its parent must NOT corrupt the total (it contributes 0)
for label, resp in {
    "cache > input": chat(prompt_tokens=1000, completion_tokens=50, total_tokens=1050, prompt_tokens_details=NS(cached_tokens=2000)),
    "reasoning > output": chat(prompt_tokens=10, completion_tokens=100, total_tokens=110, completion_tokens_details=NS(reasoning_tokens=999)),
}.items():
    ev = run(resp, CHAT)
    assert_universal(label, ev)
    check(ev.event_total_mismatch == 0, f"chat malformed [{label}]: oversized subtotal never breaks the total")

# ---------------- detectors must FIRE on the pathological payloads ----------------
def expect_flag(label, response, adapter, flag):
    ev = run(response, adapter)
    assert_universal(label, ev)
    check(flag in ev.data_quality_flags, f"detector [{label}]: raises {flag} (flags: {ev.data_quality_flags})")
    return ev


expect_flag("inconsistent total", chat(prompt_tokens=100, completion_tokens=20, total_tokens=99), CHAT, "provider_total_mismatch")
expect_flag("unknown usage field", chat(prompt_tokens=100, completion_tokens=20, total_tokens=120, super_tokens=999), CHAT, "provider_schema_drift")
expect_flag("usage missing (stream no include_usage)", NS(model="m", choices=[]), CHAT, "raw_usage_missing")
expect_flag("usage is empty list", NS(model="m", usage=[]), CHAT, "raw_usage_missing")

# ---------------- corrupt values fail LOUD (normalization_error), never crash, never wrong ----------------
for label, resp in {
    "negative quantity": chat(prompt_tokens=-5, completion_tokens=20, total_tokens=15),
    "bool disguised as int": chat(prompt_tokens=True, completion_tokens=20, total_tokens=21),
    "float quantity": chat(prompt_tokens=10.5, completion_tokens=20, total_tokens=30),
    "dangling subtotal (cache, no input)": NS(model="m", usage=NS(completion_tokens=50, total_tokens=50, prompt_tokens_details=NS(cached_tokens=100))),
}.items():
    ev = run(resp, CHAT)  # must not raise
    check("normalization_error" in ev.data_quality_flags, f"corrupt [{label}]: becomes normalization_error, no crash")
    # a rejected payload yields NO quantities, so it contributes 0 whatever authority is claimed —
    # a corrupt count can never leak into a total.
    check(len(ev.quantities) == 0 and ev.event_contributing_tokens == 0, f"corrupt [{label}]: no quantities, contributes 0")

# missing total -> honest unknown (None), never a fabricated 0-mismatch
ev = run(NS(model="m", usage=NS(prompt_tokens=100, completion_tokens=20)), CHAT)
check(ev.event_total_mismatch is None, "no total -> mismatch is None (not a fabricated reconciliation)")
check(ev.event_contributing_tokens == 120, "no total -> still counts the real input+output")

# ---------------- responses API surface ----------------
def resp_body(**usage):
    return NS(model="gpt-5-mini", usage=NS(**usage))


ev = run(resp_body(input_tokens=200, output_tokens=100, total_tokens=300,
                   input_tokens_details=NS(cached_tokens=150), output_tokens_details=NS(reasoning_tokens=40)), RESP)
assert_universal("responses cache+reasoning", ev)
check(ev.event_total_mismatch == 0 and ev.event_contributing_tokens == 300, "responses: cache+reasoning reconcile to 300")
expect_flag("responses inconsistent total", resp_body(input_tokens=200, output_tokens=100, total_tokens=250), RESP, "provider_total_mismatch")

# ---------------- embeddings surface ----------------
ev = run(NS(model="text-embedding-3-large", usage=NS(prompt_tokens=512, total_tokens=512)), EMB)
assert_universal("embeddings", ev)
check(ev.event_total_mismatch == 0 and ev.event_contributing_tokens == 512, "embeddings: 512 tokens reconcile")
check([q.token_type.value for q in ev.quantities] == ["embedding"], "embeddings: a single embedding quantity, no output")
ev = run(NS(model="m", usage=NS(prompt_tokens=512)), EMB)
check(ev.event_total_mismatch is None and ev.event_contributing_tokens == 512, "embeddings without total: honest, still counted")

raise SystemExit(check.report("RESULT test_azure_edge_matrix"))
