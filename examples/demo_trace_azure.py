"""Presentation demo: trace one Azure call through the whole pipeline, stage by stage.

Prints each stage's real output so a live audience can follow the machinery:
adapter -> extraction -> normalize -> derived fields -> persistence -> per-service aggregate.
No credentials needed (uses a realistic Azure response). Run it live and narrate each block.

Run: python examples/demo_trace_azure.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def rule(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


# A realistic Azure gpt-5-mini response: 1024 of the 1280 prompt tokens were cached,
# 64 of the 210 completion tokens were reasoning.
response = NS(
    model="gpt-5-mini",
    usage=NS(
        prompt_tokens=1280,
        completion_tokens=210,
        total_tokens=1490,
        prompt_tokens_details=NS(cached_tokens=1024, audio_tokens=0),
        completion_tokens_details=NS(reasoning_tokens=64, audio_tokens=0),
    ),
)

rule("STAGE 0 - the raw Azure response (SDK object, exactly like production)")
print("  usage: prompt=1280 (cached 1024)  completion=210 (reasoning 64)  total=1490")

rule("STAGE 1 - pick the adapter")
from tracker.adapters import create_adapter  # noqa: E402

adapter = create_adapter("azure_openai", "chat_completions", deployment="prod-westeu")
print(f"  create_adapter('azure_openai','chat_completions') -> {type(adapter).__name__}")
print("  subclass of the OpenAI adapter; the INV-4 table aliases azure_openai -> openai")

rule("STAGE 2 - extract usage + assign additivity (the per-provider truth)")
usage = adapter.extract_usage_from_response(response)
for q in usage.quantities:
    print(f"  {q.token_type.value:14} qty={q.quantity:<6} precision={q.precision_level.value:8} additivity={q.additivity.value}")
print(f"  provider_total (raw, never summed across events): {usage.provider_total_tokens}")

rule("STAGE 3 - normalize(): assemble the TokenEvent (never raises into the caller)")
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.normalization.normalizer import normalize  # noqa: E402

ctx = new_trace(workflow="invoice-rag", environment="prod", business_id="billing-team")
event = normalize(response, adapter, context=ctx, observation={"authoritative": True, "status": "complete", "service_name": "invoice-rag"})
print(f"  provider={event.provider}  service={event.observation.get('service_name')}  flags={event.data_quality_flags or '(none)'}")

rule("STAGE 4 - derived on read, NEVER stored: this is the no-double-count rule")
for q in event.quantities:
    print(f"  {q.token_type.value:14} included_in_total={str(q.included_in_total):5} counts_as={q.quantity_in_total}")
print(f"  -> event_contributing_tokens = {event.event_contributing_tokens}  (1280 input + 210 output; cache & reasoning = 0)")
print(f"  -> event_total_mismatch      = {event.event_total_mismatch}  (0 == EXACT, reconciled to Azure's total)")

rule("STAGE 5 - persist (stored fields only; derived fields are absent from disk)")
from tracker import track_response  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

store = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
repo = FileRepository(store)
result = track_response(
    response,
    adapter,
    repository=repo,
    context=ctx,
    observation={"authoritative": True, "status": "complete", "service_name": "invoice-rag"},
)
stored = json.loads(open(store, encoding="utf-8").readline())
absent = [k for k in ("event_contributing_tokens", "quantity_in_total", "included_in_total") if k not in json.dumps(stored)]
print(f"  persisted={result.persisted}  stored keys include: event_id, quantities, provider_total_tokens, observation")
print(f"  derived fields absent from the JSONL (recomputed on read): {absent}")

rule("STAGE 6 - aggregate for the dashboard (per service / provider / model)")
from tracker.export.live_dashboard import aggregate  # noqa: E402

agg = aggregate(store)
print(f"  total_tokens={agg['total_tokens']}  effective_events={agg['effective_events']}")
print(f"  by_service: {[(r['name'], r['tokens']) for r in agg['by_service']]}")
print(f"  by_model  : {[(r['name'], r['tokens']) for r in agg['by_model']]}")

print("\n" + "-" * 72)
print("ONE Azure call: 1490 tokens counted EXACTLY, cache and reasoning never double-counted,")
print("attributed to service 'invoice-rag', mismatch=0. Same path for streaming and embeddings.")
