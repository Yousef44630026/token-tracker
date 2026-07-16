"""Collector API entry point — stdlib http.server, no web framework. (Phase 8)

The receiving end of the collector pipeline: a small HTTP server that accepts token events
(the dicts a CollectorClient buffers via ``TokenEvent.to_dict``), persists them through the
JSONL FileRepository, and answers the ``transport(batch) -> acked_ids`` contract the client
expects. Built on ``http.server`` only — per the hard constraint, no FastAPI/Flask.

Routes:
    GET  /healthz      -> {"status": "ok"}
    GET  /v1/stats     -> {"events": N, "total": T, "traces": {trace_id: total}}
    GET  /v1/stats?summary=1 -> cached {"events": N, "total": T}
    POST /v1/events    -> body is one event dict or a list; returns
                          {"acked": [valid event_ids], "persisted": [new event_ids],
                           "rejected": count}.
                          Malformed JSON -> 400; a malformed item inside a batch is skipped
                          (the valid ones are still accepted), so one bad event never drops a
                          whole batch. When configured, rejected raw items are written to a
                          dead-letter JSONL file for audit.

``make_http_transport(url)`` is the matching client transport (stdlib urllib) so the loop
CollectorClient -> HTTP -> here -> FileRepository can be wired end-to-end.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlsplit

# Direct-script compatibility only; installed/imported usage does not mutate sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker.derive.derived_fields import event_contributing_tokens  # noqa: E402
from tracker.derive.effective_events import iter_effective_events  # noqa: E402
from tracker.models.token_event import TokenEvent  # noqa: E402
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository  # noqa: E402

EVENTS_PATH = "/v1/events"
DEFAULT_MAX_BODY_BYTES = 1_048_576
DEFAULT_MAX_BATCH_SIZE = 1000
DEFAULT_REQUEST_TIMEOUT_S = 30.0


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


Repository = FileRepository | PartitionedFileRepository


def _make_handler(
    repo: Repository,
    *,
    max_body_bytes: int,
    max_batch_size: int,
    auth_token: str | None,
    request_timeout_s: float,
    dead_letter_path: str | None,
) -> type[BaseHTTPRequestHandler]:
    dead_letter_lock = threading.Lock()
    summary_lock = threading.Lock()
    summary_cache: dict[str, int] | None = None
    summary_signature: tuple[int, int] | None = None

    def _store_signature() -> tuple[int, int]:
        signature_path = repo.index_path if isinstance(repo, PartitionedFileRepository) else repo.path
        try:
            stat = os.stat(signature_path)
        except FileNotFoundError:
            return (0, 0)
        return (stat.st_size, stat.st_mtime_ns)

    def _scan_summary() -> dict[str, int]:
        raw_count = 0
        effective_count = 0
        superseded_count = 0
        total = 0
        source = repo.iter_events() if hasattr(repo, "iter_events") else repo.read_all()
        for event in iter_effective_events(source):
            raw_count += 1
            if event.superseded:
                superseded_count += 1
            elif event.is_authoritative:
                effective_count += 1
            total += event_contributing_tokens(event)
        return {
            "events": raw_count,
            "effective_events": effective_count,
            "superseded_events": superseded_count,
            "total": total,
        }

    def _summary_stats() -> dict[str, int]:
        nonlocal summary_cache, summary_signature
        with summary_lock:
            signature = _store_signature()
            if summary_cache is None or signature != summary_signature:
                summary_cache = _scan_summary()
                summary_signature = _store_signature()
            return dict(summary_cache)

    def _append_unique(events: list[TokenEvent]) -> list[str]:
        nonlocal summary_cache, summary_signature
        with summary_lock:
            appended_ids = repo.append_unique(events)
            if appended_ids:
                # A newly appended final can retire an older partial. Invalidate instead of
                # incrementing a cached total that cannot subtract the superseded contribution.
                summary_cache = None
                summary_signature = None
            return appended_ids

    def _write_dead_letters(items: list[dict[str, Any]]) -> None:
        if not dead_letter_path or not items:
            return
        parent = os.path.dirname(os.path.abspath(dead_letter_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        lines = [json.dumps(item, ensure_ascii=False) + "\n" for item in items]
        with dead_letter_lock:
            with open(dead_letter_path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(lines))

    class _Handler(BaseHTTPRequestHandler):
        server_version = "AITokenTracker/1"
        # Bounds how long a connection may sit idle (e.g. a client that declares
        # Content-Length but withholds/trickles the body) so one slow client cannot
        # tie up a ThreadingHTTPServer worker thread indefinitely.
        timeout = request_timeout_s

        def log_message(self, *args: Any) -> None:  # silence default stderr logging
            pass

        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if auth_token is None:
                return True
            supplied = self.headers.get("Authorization", "")
            return hmac.compare_digest(supplied, f"Bearer {auth_token}")

        # --- reads ---
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._send(200, {"status": "ok"})
            elif path == "/v1/stats":
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                    return
                try:
                    summary_only = parse_qs(parsed.query).get("summary", [""])[0].lower() in {"1", "true", "yes"}
                    self._send(200, _summary_stats() if summary_only else self._stats())
                except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                    self._send(500, {"error": "storage_read_failed"})
            else:
                self._send(404, {"error": "not_found"})

        def _stats(self) -> dict[str, Any]:
            traces: dict[str, int] = {}
            total = 0
            count = 0
            effective_count = 0
            superseded_count = 0
            source = repo.iter_events() if hasattr(repo, "iter_events") else repo.read_all()
            for e in iter_effective_events(source):
                count += 1
                if e.superseded:
                    superseded_count += 1
                elif e.is_authoritative:
                    effective_count += 1
                contributing = event_contributing_tokens(e)
                total += contributing
                traces[e.trace_id] = traces.get(e.trace_id, 0) + contributing
            return {
                "events": count,
                "effective_events": effective_count,
                "superseded_events": superseded_count,
                "total": total,
                "traces": traces,
            }

        # --- writes ---
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            if urlsplit(self.path).path != EVENTS_PATH:
                self._send(404, {"error": "not_found"})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.headers.get_content_type() != "application/json":
                self._send(415, {"error": "unsupported_media_type"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    raise ValueError
                if length > max_body_bytes:
                    self.close_connection = True
                    self._send(413, {"error": "payload_too_large"})
                    return
                raw_body = self.rfile.read(length)
            except TimeoutError:
                # The socket-level read timed out (a slow/incomplete client) — the
                # connection may no longer be usable, so close it without trying to
                # write a response rather than risk hanging on a dead socket.
                self.close_connection = True
                return
            except (ValueError, TypeError, UnicodeError):
                self._send(400, {"error": "invalid_json"})
                return

            try:
                data = json.loads(raw_body)
            except (ValueError, TypeError, UnicodeError, RecursionError):
                # RecursionError (e.g. deeply nested JSON) is not a ValueError subclass
                # and must be caught explicitly, or it escapes as an unhandled crash.
                self._send(400, {"error": "invalid_json"})
                return

            if not isinstance(data, (dict, list)):
                self._send(400, {"error": "invalid_payload"})
                return
            batch = data if isinstance(data, list) else [data]
            if len(batch) > max_batch_size:
                self._send(413, {"error": "batch_too_large"})
                return
            events: list[TokenEvent] = []
            acked: list[str] = []
            persisted: list[str] = []
            seen_ids: set[str] = set()
            rejected = 0
            rejected_items: list[dict[str, Any]] = []
            for item in batch:
                try:
                    event = TokenEvent.from_dict(item, require_explicit_authority=True)
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    rejected += 1
                    if dead_letter_path:
                        rejected_items.append(
                            {
                                "reason": f"{type(exc).__name__}: {exc}",
                                "item": item,
                            }
                        )
                    continue  # skip a malformed item; never fail the whole batch
                events.append(event)
                if event.event_id not in seen_ids:
                    seen_ids.add(event.event_id)
                    acked.append(event.event_id)
            if rejected_items:
                try:
                    _write_dead_letters(rejected_items)
                except OSError:
                    self._send(503, {"error": "dead_letter_write_failed", "acked": [], "rejected": rejected})
                    return
            if events:
                try:
                    persisted = _append_unique(events)
                except (OSError, ValueError, TypeError):
                    self._send(503, {"error": "storage_write_failed", "acked": [], "rejected": rejected})
                    return
            self._send(200, {"acked": acked, "persisted": persisted, "rejected": rejected})

    return _Handler


def create_server(
    repo: Repository,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    auth_token: str | None = None,
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    dead_letter_path: str | None = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) the collector HTTP server bound to ``host:port``.

    Use port 0 for an OS-assigned ephemeral port; read it back from ``server_address``.
    """
    if max_body_bytes <= 0 or max_batch_size <= 0:
        raise ValueError("server limits must be positive")
    if request_timeout_s <= 0:
        raise ValueError("request_timeout_s must be positive")
    if not _is_loopback_host(host) and not auth_token:
        raise ValueError("non-loopback collector binds require auth_token")
    server = ThreadingHTTPServer(
        (host, port),
        _make_handler(
            repo,
            max_body_bytes=max_body_bytes,
            max_batch_size=max_batch_size,
            auth_token=auth_token,
            request_timeout_s=request_timeout_s,
            dead_letter_path=dead_letter_path,
        ),
    )
    server.daemon_threads = True
    return server


def make_http_transport(
    url: str,
    timeout: float = 5.0,
    *,
    auth_token: str | None = None,
) -> Callable[[list[dict[str, Any]]], list[str]]:
    """A CollectorClient transport that POSTs a batch to ``url`` and returns the acked ids.

    On any network/HTTP error it returns ``[]`` (nothing acked), so the client requeues the
    batch — the collector's safe-failure contract is preserved.
    """

    def transport(batch: list[dict[str, Any]]) -> list[str]:
        body = json.dumps(batch).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth_token is not None:
            headers["Authorization"] = f"Bearer {auth_token}"
        req = urllib_request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
                acked = payload.get("acked", [])
                if not isinstance(acked, list):
                    return []
                return [event_id for event_id in acked if isinstance(event_id, str)]
        except Exception:  # noqa: BLE001 — best-effort delivery; failures requeue
            return []

    return transport


def _environment_flag(environment: dict[str, str], name: str, *, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


def _parser(environment: dict[str, str] | None = None) -> argparse.ArgumentParser:
    env = os.environ if environment is None else environment
    parser = argparse.ArgumentParser(description="Run the AI token tracker collector")
    parser.add_argument(
        "--store",
        default=env.get("TRACKER_STORE", "collector_events.jsonl"),
    )
    parser.add_argument("--host", default=env.get("TRACKER_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(env.get("TRACKER_PORT", "8787")),
    )
    parser.add_argument(
        "--durable",
        action=argparse.BooleanOptionalAction,
        default=_environment_flag(env, "TRACKER_DURABLE", default=True),
        help="fsync acknowledged events before returning success (default: enabled)",
    )
    parser.add_argument(
        "--partitioned-store",
        action=argparse.BooleanOptionalAction,
        default=_environment_flag(env, "TRACKER_PARTITIONED", default=False),
        help="treat --store as a date/trace-partitioned ledger root",
    )
    parser.add_argument("--auth-token", default=env.get("TRACKER_AUTH_TOKEN"))
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)

    repository_type = PartitionedFileRepository if args.partitioned_store else FileRepository
    repo = repository_type(args.store, durable=args.durable)
    server = create_server(repo, args.host, args.port, auth_token=args.auth_token)
    bound_host, bound_port = server.server_address[:2]
    print(f"collector listening on http://{bound_host}:{bound_port}{EVENTS_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
