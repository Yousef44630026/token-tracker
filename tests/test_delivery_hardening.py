"""Concurrency, idempotency, and request-boundary hardening."""

import json
import os
import sys
import threading
import time
import uuid
from urllib import error as url_error
from urllib import request as url_request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.main import create_server, make_http_transport  # noqa: E402
from tracker.collector.client import CollectorClient, CollectorConfig  # noqa: E402
from tracker.models.enums import Additivity, PrecisionLevel, TokenType, UsageSource  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.models.token_quantity import TokenQuantity  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

_failures = 0


def check(condition, message):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {message}")
    if not condition:
        _failures += 1


def event(event_id, quantity=1):
    return TokenEvent(
        event_id=event_id,
        request_correlation_id=f"request-{event_id}",
        trace_id="trace-hardening",
        span_id="span",
        quantities=[
            TokenQuantity(
                TokenType.OUTPUT,
                quantity,
                PrecisionLevel.EXACT,
                UsageSource.PROVIDER_RESPONSE,
                Additivity.TOTAL_CONTRIBUTING,
            )
        ],
        provider_total_tokens=quantity,
    )


def local_store(prefix):
    root = os.path.abspath(f".test_{prefix}_{uuid.uuid4().hex}")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "events.jsonl")


# Repositories targeting the same path serialize thread writes, even across instances.
repo_path = local_store("repo_concurrent")
thread_count = 8
per_thread = 100
write_errors = []


def writer(thread_id):
    try:
        repository = FileRepository(repo_path)
        for item in range(per_thread):
            repository.append(event(f"event-{thread_id}-{item}"))
    except Exception as exc:  # noqa: BLE001
        write_errors.append(repr(exc))


threads = [threading.Thread(target=writer, args=(index,)) for index in range(thread_count)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()

stored = FileRepository(repo_path).read_all()
check(write_errors == [], f"concurrent repositories raise no errors: {write_errors[:2]}")
check(len(stored) == thread_count * per_thread, "concurrent JSONL writes lose no events")
check(
    len({item.event_id for item in stored}) == len(stored),
    "concurrent JSONL stays uncorrupted",
)

# Idempotent append works across repository instances and within one batch.
repo_a = FileRepository(repo_path)
repo_b = FileRepository(repo_path)
check(
    repo_a.append_unique([event("unique"), event("unique")]) == ["unique"],
    "batch dedup persists once",
)
check(repo_b.append_unique([event("unique")]) == [], "cross-instance duplicate is skipped")

# A crash-truncated final line can be ignored without hiding corruption in complete lines.
tail_path = local_store("repo_tail")
tail_repo = FileRepository(tail_path)
tail_repo.append(event("complete"))
with open(tail_path, "ab") as handle:
    handle.write(b'{"event_id":')
check(
    [item.event_id for item in tail_repo.read_all()] == ["complete"],
    "truncated tail is recoverable",
)
strict_failed = False
try:
    FileRepository(tail_path, recover_truncated_tail=False).read_all()
except json.JSONDecodeError:
    strict_failed = True
check(strict_failed, "strict repository mode surfaces a truncated tail")
tail_repo.append(event("after-truncation"))
check(
    [item.event_id for item in tail_repo.read_all()] == ["complete", "after-truncation"],
    "append repairs a truncated tail before writing",
)


# record() and multiple flushers can operate concurrently without loss or duplicate sends.
class Transport:
    def __init__(self):
        self.ids = []
        self.lock = threading.Lock()

    def __call__(self, batch):
        ids = [item["event_id"] for item in batch]
        time.sleep(0.001)
        with self.lock:
            self.ids.extend(ids)
        return ids


transport = Transport()
total = 1000
collector = CollectorClient(
    transport,
    CollectorConfig(max_buffer_size=total + 10, batch_size=50, dedup_history_size=total + 10),
)
recording_done = threading.Event()


def record_all():
    for index in range(total):
        collector.record({"event_id": f"concurrent-{index}"})
    recording_done.set()


def flush_until_done():
    while not recording_done.is_set() or collector.pending:
        collector.flush()
        time.sleep(0.0005)


record_thread = threading.Thread(target=record_all)
flush_threads = [threading.Thread(target=flush_until_done) for _ in range(2)]
record_thread.start()
for thread in flush_threads:
    thread.start()
record_thread.join()
for thread in flush_threads:
    thread.join()

check(collector.sent_total == total, "concurrent record/flush delivers every event")
check(len(transport.ids) == total and len(set(transport.ids)) == total, "delivery is exactly once")


# HTTP ingestion is idempotent and enforces body/batch limits.
api_path = local_store("api_hardened")
api_repo = FileRepository(api_path)
server = create_server(api_repo, "127.0.0.1", 0, max_body_bytes=2048, max_batch_size=2)
base = f"http://127.0.0.1:{server.server_address[1]}"
threading.Thread(target=server.serve_forever, daemon=True).start()


def request(path, payload):
    body = json.dumps(payload).encode("utf-8")
    req = url_request.Request(
        base + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with url_request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except url_error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


try:
    transport_http = make_http_transport(base + "/v1/events")
    payload = event("idempotent", 7).to_dict()
    check(transport_http([payload]) == ["idempotent"], "first HTTP delivery is acknowledged")
    check(transport_http([payload]) == ["idempotent"], "duplicate HTTP delivery is acknowledged")
    check(len(api_repo.read_all()) == 1, "duplicate HTTP delivery is persisted once")

    code, _ = request("/v1/events", [payload, payload, payload])
    check(code == 413, "oversized batch is rejected")
    code, _ = request("/v1/events", {"padding": "x" * 3000})
    check(code == 413, "oversized request body is rejected")
    invalid = event("negative").to_dict()
    invalid["quantities"][0]["quantity"] = -1
    code, body = request("/v1/events", invalid)
    check(code == 200 and body["acked"] == [], "invalid model values are skipped safely")
    no_content_type = url_request.Request(
        base + "/v1/events",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    try:
        url_request.urlopen(no_content_type, timeout=5)
        media_code = 200
    except url_error.HTTPError as exc:
        media_code = exc.code
    check(media_code == 415, "non-JSON media type is rejected")
finally:
    server.shutdown()
    server.server_close()

# Optional bearer authentication protects ingestion and stats while health stays public.
auth_repo = FileRepository(local_store("api_auth"))
auth_server = create_server(auth_repo, "127.0.0.1", 0, auth_token="secret")
auth_base = f"http://127.0.0.1:{auth_server.server_address[1]}"
threading.Thread(target=auth_server.serve_forever, daemon=True).start()
try:
    check(
        make_http_transport(auth_base + "/v1/events")([event("auth").to_dict()]) == [],
        "unauthenticated transport is not acknowledged",
    )
    check(
        make_http_transport(auth_base + "/v1/events", auth_token="secret")([event("auth").to_dict()]) == ["auth"],
        "authenticated transport is acknowledged",
    )
    check(len(auth_repo.read_all()) == 1, "authenticated event is persisted")
finally:
    auth_server.shutdown()
    auth_server.server_close()

print("\nRESULT:", "all checks passed" if _failures == 0 else f"{_failures} FAILURE(S)")
sys.exit(1 if _failures else 0)
