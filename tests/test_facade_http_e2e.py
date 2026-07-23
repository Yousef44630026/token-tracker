"""Extra — public façade -> collector -> real HTTP server -> repository (full e2e).

Run: python tests/test_facade_http_e2e.py

Ties the highest-level API to the running server: track_response() fans out to a
CollectorClient whose transport POSTs to a live api.main server; after flush the events are
persisted and /v1/stats reconciles with the model.
"""

import atexit
import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib import request as urlreq  # noqa: E402

from api.main import create_server, make_http_transport  # noqa: E402
from tracker.adapters.openai_chat_completions_adapter import OpenAIChatCompletionsAdapter  # noqa: E402
from tracker.collector.client import CollectorClient  # noqa: E402
from tracker.context.propagation import new_trace  # noqa: E402
from tracker.models.trace import Trace  # noqa: E402
from tracker.service import track_response  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0
PAYLOAD = {"usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}}
configured_workspace = os.environ.get("TRACKER_TEST_WORKSPACE")
work = Path(configured_workspace) if configured_workspace else Path.cwd() / f".test_facade_e2e_{uuid.uuid4().hex}"
work.mkdir(parents=True, exist_ok=True)
if configured_workspace is None:
    atexit.register(shutil.rmtree, work, True)


def check(cond, msg):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        _failures += 1


def get(base, path):
    with urlreq.urlopen(base + path, timeout=5) as resp:
        return json.loads(resp.read())


repo = FileRepository(str(work / "events.jsonl"))
server = create_server(repo, "127.0.0.1", 0)
base = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    transport = make_http_transport(base + "/v1/events")
    collector = CollectorClient(transport)
    adapter = OpenAIChatCompletionsAdapter()
    trace = Trace(trace_id="facade-e2e")

    # the application code: one façade call per provider response
    n = 5
    for _ in range(n):
        ctx = new_trace(trace_id="facade-e2e", workflow="e2e")
        result = track_response(PAYLOAD, adapter, context=ctx, trace=trace, collector=collector)
        check(result.collected is True, "façade buffered the event into the collector")

    check(len(trace.events) == n and collector.pending == n, f"{n} events tracked and buffered")
    check(repo.read_all() == [], "nothing persisted before flush")

    # background flush -> delivered over HTTP -> persisted server-side
    while collector.pending:
        collector.flush()
    check(collector.sent_total == n, f"all {n} events delivered over HTTP")

    persisted = repo.read_all()
    check(len(persisted) == n, "server persisted every delivered event")

    stats = get(base, "/v1/stats")
    check(stats["events"] == n and stats["total"] == n * 10, f"/v1/stats reconciles end-to-end ({n*10})")
    check(stats["traces"]["facade-e2e"] == n * 10, "per-trace total reconciles")
finally:
    server.shutdown()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
