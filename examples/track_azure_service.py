"""Drop-in: track a real Azure OpenAI service's token usage, attributed per service.

This is the exact integration pattern for a Deloitte app whose code you own — NO proxy.
You add ONE call (`track_response`) after your existing Azure call.

Modes:
  * REAL   — if AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_DEPLOYMENT are set,
             it makes a genuine Azure call and tracks the real usage.
  * DEMO   — otherwise it uses a realistic Azure response so the tracking loop and the
             per-service dashboard can be proven end-to-end without credentials.

Run:
  python examples/track_azure_service.py --service invoice-rag --store C:\\path\\to\\ledger.jsonl
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tracker import track_response  # noqa: E402
from tracker.adapters import create_adapter  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402


def _real_azure_call(prompt: str) -> object:
    """Your existing Azure call, unchanged. Returns the SDK response (with .usage)."""
    from openai import AzureOpenAI

    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    )
    return client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        messages=[{"role": "user", "content": prompt}],
    )


def _demo_response() -> dict:
    """A realistic Azure Chat Completions response shape (used when no credentials are set)."""
    return {
        "model": "gpt-5-mini",
        "usage": {
            "prompt_tokens": 1280,
            "completion_tokens": 210,
            "total_tokens": 1490,
            "prompt_tokens_details": {"cached_tokens": 1024},
            "completion_tokens_details": {"reasoning_tokens": 64},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="invoice-rag", help="service_name attribution key")
    parser.add_argument("--environment", default="prod")
    parser.add_argument("--business-id", default="billing-team")
    parser.add_argument("--store", default=os.environ.get("TRACKER_STORE", "azure_demo_ledger.jsonl"))
    parser.add_argument("--prompt", default="Summarize the attached invoice.")
    parser.add_argument("--deployment", default=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini"))
    args = parser.parse_args()

    real = all(k in os.environ for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"))
    response = _real_azure_call(args.prompt) if real else _demo_response()

    # --- the tracker wiring: one adapter, one context, ONE track_response call ---
    repo = FileRepository(args.store)
    adapter = create_adapter("azure_openai", "chat_completions")
    ctx = new_trace(workflow=args.service, environment=args.environment, business_id=args.business_id)

    result = track_response(
        response,
        adapter,
        repository=repo,
        context=ctx,
        # per-service attribution the dashboard groups by; never guessed, never zero if absent
        observation={
            "authoritative": True,
            "status": "complete",
            "service_name": args.service,
            "cloud_provider": "azure",
            "azure_deployment": args.deployment,
        },
    )

    event = result.event
    print(f"mode                 : {'REAL Azure call' if real else 'DEMO (no credentials set)'}")
    print(f"service_name         : {args.service}")
    print(f"provider / surface   : {event.provider} / {event.api_surface}")
    print(f"contributing tokens  : {event.event_contributing_tokens}")
    print(f"provider_total_tokens: {event.provider_total_tokens}  (mismatch: {event.event_total_mismatch})")
    print(f"quantities           : {[(q.token_type.value, q.quantity, q.additivity.value) for q in event.quantities]}")
    print(f"persisted to ledger  : {result.persisted}  -> {args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
