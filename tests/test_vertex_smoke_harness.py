"""Vertex live-proof harness tests with an injected HTTP transport."""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._harness import make_checker  # noqa: E402
from tracker.ops.vertex_smoke import run_vertex_smoke  # noqa: E402
from tracker.storage.file_repository import FileRepository  # noqa: E402

check = make_checker()
TEST_ACCESS_TOKEN = "unit-token"


class FakeResponse:
    def __init__(self, payload: dict | None = None, *, raw: bytes | None = None) -> None:
        self.payload = payload
        self.raw = raw
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.raw if self.raw is not None else json.dumps(self.payload).encode("utf-8")


def fake_opener(req, timeout):
    assert timeout > 0
    assert req.get_header("Authorization") == "Bearer " + TEST_ACCESS_TOKEN
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data.decode("utf-8"))
    if ":generateContent" in req.full_url:
        assert body["generationConfig"]["maxOutputTokens"] == 128
        return FakeResponse(
            {
                "modelVersion": "gemini-unit",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "Il reste 109 unites."}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "totalTokenCount": 13},
            }
        )
    if ":streamGenerateContent" in req.full_url:
        assert req.full_url.endswith("?alt=sse")
        chunks = [
            {
                "modelVersion": "gemini-unit",
                "candidates": [{"content": {"parts": [{"text": "Il reste "}]}}],
            },
            {
                "modelVersion": "gemini-unit",
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "109 unites."}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4, "totalTokenCount": 15},
            },
        ]
        raw = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode("utf-8")
        return FakeResponse(raw=raw)
    if ":embedContent" in req.full_url:
        assert body["embedContentConfig"]["taskType"] == "RETRIEVAL_DOCUMENT"
        return FakeResponse(
            {
                "modelVersion": "embed-unit",
                "embedding": {"values": [0.1, 0.2]},
                "usageMetadata": {"promptTokenCount": 7, "totalTokenCount": 7},
                "truncated": False,
            }
        )
    raise AssertionError(f"unexpected Vertex URL: {req.full_url}")


def incomplete_stream_opener(req, timeout):
    if ":streamGenerateContent" in req.full_url:
        raw = b'data: {"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}\n\n'
        return FakeResponse(raw=raw)
    return fake_opener(req, timeout)


root = Path(f".test_vertex_smoke_{uuid.uuid4().hex}").resolve()
root.mkdir(parents=True, exist_ok=True)
env = {
    "VERTEX_PROJECT_ID": "unit-project",
    "VERTEX_LOCATION": "europe-west1",
    "VERTEX_GENERATIVE_MODEL": "gemini-unit",
    "VERTEX_EMBEDDING_MODEL": "embed-unit",
    "VERTEX_ACCESS_TOKEN": TEST_ACCESS_TOKEN,
}

try:
    summary = run_vertex_smoke(out_dir=str(root / "success"), environment=env, opener=fake_opener, require_live=True)
    check(summary.passed is True, "all configured Vertex proof surfaces pass")
    check(summary.ran_count == 3 and summary.skipped_count == 0, "generation, streaming, and embeddings all ran")
    check(summary.event_count == 3, "one auditable event is emitted per Vertex surface")
    check(summary.observed_total_contributing_tokens == 35, "all Vertex provider totals reconcile exactly")
    events = FileRepository(summary.artifacts["events_jsonl"]).read_all()
    check(all(event.provider == "vertex_ai" for event in events), "live proof keeps the Vertex provider label")
    check(all(event.event_total_mismatch == 0 for event in events), "every Vertex event reconciles to provider usage")
    stream_event = next(event for event in events if event.observation.get("scenario") == "vertex-stream-proof")
    check(
        {quantity.usage_source.value for quantity in stream_event.quantities} == {"provider_stream_final"},
        "Vertex stream keeps terminal provider provenance",
    )
    config = Path(summary.artifacts["config"]).read_text(encoding="utf-8")
    check(TEST_ACCESS_TOKEN not in config, "Vertex audit config never serializes the access token")
    check('"access_token": "present"' in config, "audit records token presence without credential material")
    embedding_artifact = json.loads(
        Path(next(result.artifact for result in summary.results if result.surface == "embeddings")).read_text(
            encoding="utf-8"
        )
    )
    check(
        embedding_artifact["response"]["embedding"]["values"] == {"redacted_vector_length": 2},
        "Vertex audit records vector dimensions without serializing the embedding",
    )

    cut = run_vertex_smoke(
        out_dir=str(root / "cut"),
        environment=env,
        opener=incomplete_stream_opener,
        require_live=True,
        surfaces=["stream"],
    )
    check(cut.passed is False, "stream without terminal usage fails closed")
    check(
        "provider_stream_usage_missing" in cut.results[0].data_quality_flags,
        "missing Vertex terminal usage raises the canonical quality flag",
    )

    missing = run_vertex_smoke(
        out_dir=str(root / "missing"),
        environment={},
        opener=fake_opener,
        dry_run=True,
        require_live=True,
    )
    check(missing.passed is False and missing.ran_count == 0, "require-live refuses a configuration-only Vertex run")
    check(missing.skipped_count == 3, "every unavailable Vertex surface is reported explicitly")

    invalid_location = run_vertex_smoke(
        out_dir=str(root / "invalid-location"),
        environment={**env, "VERTEX_LOCATION": "https://attacker.invalid"},
        opener=fake_opener,
        require_live=True,
        surfaces=["generate"],
    )
    check(invalid_location.passed is False, "invalid Vertex location fails before any network request")
    check(invalid_location.results[0].detail == "invalid VERTEX_LOCATION", "location validation explains the failure")
finally:
    shutil.rmtree(root, ignore_errors=True)

raise SystemExit(check.report("RESULT test_vertex_smoke_harness"))
