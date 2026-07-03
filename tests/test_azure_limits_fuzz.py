"""DEEP Azure OpenAI fuzz — stress the real, freshly-captured Responses API shape and the
documented Chat Completions shape at their limits. Not generic parametrized loops: each part
is a realistic scenario (a multi-deployment helpdesk tenant, a mutation fuzzer anchored on our
OWN real captured payload, hand-crafted Azure-specific adversarial shapes, and a shuffle/
concurrency stress) built to find real problems, not to restate the obvious.

Run: & "C:\\Users\\yerabhaoui\\python-portable\\python.exe" tests\\test_azure_limits_fuzz.py
Seed sweep: $env:FUZZ_SEED = "<n>" before running, or see SEEDS_TO_SWEEP below.

Azure-specific realities exercised here:
  - Azure routes by DEPLOYMENT NAME (arbitrary, tenant-chosen) while the response body carries
    the underlying MODEL — both must be tracked without collision, per deployment, per trace.
  - Azure attaches `content_filter_results` / `prompt_filter_results` blocks that are NOT usage
    data but sit right next to it in the response; a naive "usage" reader must not be fooled by
    decoy fields nested inside them.
  - additivity for azure_openai is aliased to openai's table (INV-4) — reasoning/cached must
    stay subtotals under Azure's provider label exactly as under "openai" directly.
  - the mutation fuzzer in Part 2 mutates our OWN real captured gpt-5-mini Responses payload
    (tests/fixtures/realistic/azure_openai_responses.REAL.json), not an invented shape.
"""

import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.adapters.azure_openai_chat_completions_adapter import AzureOpenAIChatCompletionsAdapter  # noqa: E402
from tracker.adapters.azure_openai_responses_adapter import AzureOpenAIResponsesAdapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.derive.trace_rollup import observed_total_contributing_tokens  # noqa: E402
from tracker.models.enums import TokenType  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

_failures = 0
_checks = 0
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
REAL_FIXTURE = os.path.join(FIXTURES, "realistic", "azure_openai_responses.REAL.json")


def check(cond, msg):
    global _failures, _checks
    _checks += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def q(ev, tt):
    return next((x for x in ev.quantities if x.token_type == tt), None)


# =====================================================================================
# PART 1 — "Enterprise helpdesk" tenant: 3 deployments, growing cache, content-filter
# blocks interleaved, deployment/trace isolation must hold under load.
# =====================================================================================
print("--- Part 1: multi-deployment helpdesk tenant (Chat Completions) ---")

SEED = int(os.environ.get("FUZZ_SEED", "20260702"))
rng = random.Random(SEED)

DEPLOYMENTS = [
    ("prod-gpt4o", "gpt-4o-2024-08-06", False),
    ("prod-gpt4o-mini", "gpt-4o-mini-2024-07-18", False),
    ("dev-o3-mini-reasoning", "o3-mini-2025-01-31", True),
]

adapters = {dep: AzureOpenAIChatCompletionsAdapter(deployment=dep) for dep, _, _ in DEPLOYMENTS}
expected_by_deployment = {dep: 0 for dep, _, _ in DEPLOYMENTS}
observed_by_deployment = {dep: 0 for dep, _, _ in DEPLOYMENTS}
seen_deployment_tags: set[str] = set()

N_CONVERSATIONS = 40
for conv in range(N_CONVERSATIONS):
    dep, model, reasoning_capable = rng.choice(DEPLOYMENTS)
    adapter = adapters[dep]
    tr = Trace(trace_id=f"conv-{conv}")
    n_turns = rng.randint(1, 6)
    running_prompt = rng.randint(200, 900)  # grows as history accumulates
    for turn in range(n_turns):
        blocked = rng.random() < 0.1
        ctx = new_trace(trace_id=tr.trace_id)
        if blocked:
            # Azure content-filter block: no `usage`, but a `prompt_filter_results` block
            # sits where usage would be. Must be flagged, not silently zero-cost.
            response = {
                "id": f"chatcmpl-{conv}-{turn}",
                "model": model,
                "choices": [],
                "prompt_filter_results": [{"prompt_index": 0, "content_filter_results": {"hate": {"filtered": True, "severity": "high"}}}],
            }
            ev = normalize(response, adapter, context=ctx)
            check("raw_usage_missing" in ev.data_quality_flags, f"conv{conv}/turn{turn}: content-filter block flagged raw_usage_missing")
            check(ev.event_contributing_tokens == 0, f"conv{conv}/turn{turn}: blocked turn contributes 0")
            continue

        running_prompt += rng.randint(20, 150)
        cached = min(running_prompt - 10, int(running_prompt * rng.uniform(0.0, 0.85))) if turn > 0 else 0
        output = rng.randint(15, 400)
        reasoning = int(output * rng.uniform(0.2, 0.8)) if (reasoning_capable and rng.random() < 0.7) else 0
        usage = {
            "prompt_tokens": running_prompt,
            "completion_tokens": output,
            "total_tokens": running_prompt + output,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        }
        response = {"id": f"chatcmpl-{conv}-{turn}", "model": model, "choices": [{"finish_reason": "stop"}], "usage": usage}
        ev = normalize(response, adapter, context=ctx)
        tr.add_event(ev)
        check(ev.event_total_mismatch == 0, f"conv{conv}/turn{turn} ({dep}): reconciles")
        for quantity in ev.quantities:
            tag = quantity.metadata.get("azure_deployment")
            check(tag == dep, f"conv{conv}/turn{turn}: quantity tagged with its OWN deployment ({tag} == {dep})")
            seen_deployment_tags.add(tag)
        expected_by_deployment[dep] += running_prompt + output
    observed_by_deployment[dep] += observed_total_contributing_tokens(tr)

check(seen_deployment_tags == {d for d, _, _ in DEPLOYMENTS}, "all 3 deployments produced tagged quantities")
for dep, _, _ in DEPLOYMENTS:
    check(
        observed_by_deployment[dep] == expected_by_deployment[dep],
        f"{dep}: aggregated total matches ({observed_by_deployment[dep]} != {expected_by_deployment[dep]})",
    )
grand_expected = sum(expected_by_deployment.values())
grand_observed = sum(observed_by_deployment.values())
check(grand_observed == grand_expected, f"helpdesk tenant grand total ({grand_observed} != {grand_expected}), no cross-deployment leakage")
print(f"[INFO] Part 1: {N_CONVERSATIONS} conversations across 3 deployments (seed={SEED}).")


# =====================================================================================
# PART 2 — mutation fuzz anchored on OUR OWN real captured Responses payload (gpt-5-mini).
# =====================================================================================
print("\n--- Part 2: mutation fuzz on the real captured Responses payload ---")

with open(REAL_FIXTURE, encoding="utf-8") as f:
    real_wrapped = json.load(f)
REAL_RESPONSE = real_wrapped["response"]
REAL_DEPLOYMENT = real_wrapped["_deployment"]

SCALE_BUCKETS = [
    (0, 0),
    (0, 1),
    (1, 1),
    (1, 50),
    (50, 5000),
    (5000, 200000),
    (10**6, 10**7),
    (10**9, 10**9 + 5),
    (10**12, 10**12 + 1),
]

resp_adapter = AzureOpenAIResponsesAdapter(deployment=REAL_DEPLOYMENT)
N_MUTATIONS = 300
negative_injections = 0
huge_scale_checked = False
for i in range(N_MUTATIONS):
    payload = copy.deepcopy(REAL_RESPONSE)
    lo, hi = rng.choice(SCALE_BUCKETS)
    input_t = rng.randint(lo, hi) if hi > lo else lo
    lo, hi = rng.choice(SCALE_BUCKETS)
    output_t = rng.randint(lo, hi) if hi > lo else lo
    cached_t = rng.randint(0, input_t) if input_t > 0 and rng.random() < 0.6 else 0
    reasoning_t = rng.randint(0, output_t) if output_t > 0 and rng.random() < 0.6 else 0

    inject_negative = rng.random() < 0.03
    if inject_negative:
        negative_injections += 1
        input_t = -abs(input_t or 1)

    payload["usage"] = {
        "input_tokens": input_t,
        "input_tokens_details": {"cached_tokens": cached_t},
        "output_tokens": output_t,
        "output_tokens_details": {"reasoning_tokens": reasoning_t},
        "total_tokens": input_t + output_t,
    }

    ev = normalize(payload, resp_adapter, context=new_trace())

    if inject_negative:
        check(ev.event_contributing_tokens == 0, f"mutation #{i}: negative input rejected, contributes 0 (no crash)")
        check("normalization_error" in ev.data_quality_flags, f"mutation #{i}: negative input flagged normalization_error")
        continue

    check(ev.event_total_mismatch == 0, f"mutation #{i}: reconciles ({input_t=} {output_t=} {cached_t=} {reasoning_t=})")
    check(ev.event_contributing_tokens == input_t + output_t, f"mutation #{i}: contributing == input+output exactly")
    cached_q = q(ev, TokenType.CACHED_INPUT)
    if cached_q is not None:
        check(cached_q.quantity_in_total == 0, f"mutation #{i}: cached subtotal excluded regardless of magnitude")
    reasoning_q = q(ev, TokenType.REASONING)
    if reasoning_q is not None:
        check(reasoning_q.quantity_in_total == 0, f"mutation #{i}: reasoning subtotal excluded regardless of magnitude")
    if max(input_t, output_t) >= 10**9:
        huge_scale_checked = True

check(negative_injections > 0, f"Part 2: negative-injection branch actually exercised ({negative_injections} times)")
check(huge_scale_checked, "Part 2: at least one mutation reached >= 10^9 scale (no overflow/precision loss)")
print(f"[INFO] Part 2: {N_MUTATIONS} mutations of the real gpt-5-mini payload, {negative_injections} negative injections (seed={SEED}).")


# =====================================================================================
# PART 3 — hand-crafted Azure-specific adversarial shapes (not random — targeted).
# =====================================================================================
print("\n--- Part 3: Azure-specific adversarial shapes ---")

# 3a: a decoy field named exactly like a usage field, buried inside content_filter_results,
# while the real top-level `usage` is entirely absent. Must NOT be mistaken for real usage.
decoy_payload = {
    "id": "chatcmpl-decoy",
    "model": "gpt-4o-2024-08-06",
    "choices": [
        {
            "finish_reason": "stop",
            "content_filter_results": {
                "custom_blocklists": {"filtered": False, "details": [{"prompt_tokens": 999999, "id": "blk1"}]},
            },
        }
    ],
}
ev = normalize(decoy_payload, AzureOpenAIChatCompletionsAdapter(deployment="prod-gpt4o"), context=new_trace())
check("raw_usage_missing" in ev.data_quality_flags, "3a: decoy 'prompt_tokens' inside content_filter_results is NOT read as usage")
check(ev.event_contributing_tokens == 0, "3a: decoy payload contributes 0, not 999999")

# 3b: a hypothetical Azure API-version rename of the reasoning subfield. The top-level total
# must still reconcile (output_tokens already includes reasoning internally); only the
# sub-breakdown detail is lost, and it must be lost SILENTLY-SAFE (None), never fabricated.
renamed_payload = copy.deepcopy(REAL_RESPONSE)
renamed_payload["usage"] = {
    "input_tokens": 500,
    "input_tokens_details": {"cached_tokens": 100},
    "output_tokens": 200,
    "output_tokens_details": {"reasoning_token_count": 80},  # renamed key
    "total_tokens": 700,
}
ev = normalize(renamed_payload, AzureOpenAIResponsesAdapter(deployment=REAL_DEPLOYMENT), context=new_trace())
check(q(ev, TokenType.REASONING) is None, "3b: renamed reasoning subfield yields no fabricated reasoning quantity")
check(
    ev.event_contributing_tokens == 700 and ev.event_total_mismatch == 0,
    "3b: top-level total still reconciles despite lost subtotal detail",
)
check(q(ev, TokenType.CACHED_INPUT).quantity == 100, "3b: unrelated cached field unaffected by the rename elsewhere")

# 3c: a fully content-filtered completion — output blocked to empty, but usage still reports
# output_tokens=0 (a measured zero, not an unknown). Must be treated as EXACT 0, not dropped.
blocked_output_payload = copy.deepcopy(REAL_RESPONSE)
blocked_output_payload["status"] = "incomplete"
blocked_output_payload["output"] = []
blocked_output_payload["usage"] = {
    "input_tokens": 42,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 0,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 42,
}
ev = normalize(blocked_output_payload, AzureOpenAIResponsesAdapter(deployment=REAL_DEPLOYMENT), context=new_trace())
out_q = q(ev, TokenType.OUTPUT)
check(out_q is not None and out_q.quantity == 0, "3c: measured output=0 is a real quantity, not omitted")
check(ev.event_contributing_tokens == 42 and ev.event_total_mismatch == 0, "3c: blocked-output turn still reconciles (input-only cost)")

print("[INFO] Part 3: 3 hand-crafted Azure-specific adversarial shapes.")


# =====================================================================================
# PART 4 — shuffle stress: insertion order must never affect totals (commutativity).
# =====================================================================================
print("\n--- Part 4: shuffle/concurrency stress across deployments ---")

batch = []
expected_total_p4 = 0
for i in range(200):
    dep, model, reasoning_capable = rng.choice(DEPLOYMENTS)
    inp, out = rng.randint(10, 8000), rng.randint(1, 1200)
    cached = rng.randint(0, inp) if rng.random() < 0.5 else 0
    reasoning = rng.randint(0, out) if reasoning_capable and rng.random() < 0.5 else 0
    usage = {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "total_tokens": inp + out,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }
    response = {"id": f"chatcmpl-shuffle-{i}", "model": model, "choices": [{"finish_reason": "stop"}], "usage": usage}
    batch.append((dep, response))
    expected_total_p4 += inp + out

for trial in range(5):
    shuffled = batch[:]
    rng.shuffle(shuffled)
    tr = Trace(trace_id=f"shuffle-trial-{trial}")
    for dep, response in shuffled:
        ev = normalize(response, adapters[dep], context=new_trace(trace_id=tr.trace_id))
        tr.add_event(ev)
    got = observed_total_contributing_tokens(tr)
    check(got == expected_total_p4, f"shuffle trial {trial}: order-independent total ({got} != {expected_total_p4})")

print(f"[INFO] Part 4: {len(batch)} events, 5 independent shuffles, all reconciling to the same total.")

print(f"\n[INFO] total checks run: {_checks}")
print("RESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
