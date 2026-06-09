"""
Phase 3 integration tests — Upload Pipeline (Launcher)

Tests the full CloudEvent → validate → Firestore update → GCS write flow
against real GCP resources.

Real:
  - GCS: test JSONL written to real input bucket, errors.jsonl read from real
    output bucket
  - Firestore: job doc created and updated via real Firestore

Mocked:
  - spawn_execution_job: no execution container image exists yet (Phase 4).
    Only the K8s API call is patched — everything before it is real.
"""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# conftest sets sys.path and loads .env
from tests.integration.conftest import INPUT_BUCKET, OUTPUT_BUCKET, TEST_CLIENT_ID

from shared.firestore import create_job, get_job
from shared.gcs import stream_read, write_bytes
from shared.models.job import JobRecord, JobStatus

# ── load services/launcher/main.py under a unique module name ─────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "launcher_main_integ",
    str(PROJECT_ROOT / "services" / "launcher" / "main.py"),
)
assert _spec and _spec.loader
launcher_main = importlib.util.module_from_spec(_spec)
sys.modules["launcher_main_integ"] = launcher_main
_spec.loader.exec_module(launcher_main)  # type: ignore[union-attr]


# ── test data ─────────────────────────────────────────────────────────────────

# 5 valid prompts + 2 invalid lines
_MIXED_JSONL = "\n".join([
    json.dumps({"prompt_id": 1, "prompt": "What is 2+2?"}),
    json.dumps({"prompt_id": 2, "prompt": "Name the planets."}),
    json.dumps({"prompt_id": 3, "prompt": "Write a haiku about clouds."}),
    json.dumps({"prompt_id": 4, "prompt": "Translate 'hello' to French."}),
    json.dumps({"prompt_id": 5, "prompt": "Summarize the water cycle."}),
    json.dumps({"bad_key": "missing prompt_id"}),          # invalid — no prompt_id
    json.dumps({"also_bad": True}),                        # invalid — no prompt or prompt_id
]).encode()

_VALID_ONLY_JSONL = "\n".join([
    json.dumps({"prompt_id": 1, "prompt": "Hello world"}),
    json.dumps({"prompt_id": 2, "prompt": "What is AI?"}),
]).encode()

_FULLY_INVALID = b"this is not json at all\nneither is this line\n"


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_job_record(job_id: str) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        client_id=TEST_CLIENT_ID,
        provider="openai",
        model="gpt-4o",
        status=JobStatus.AWAITING_UPLOAD,
        max_retries=3,
        rpm=60,
        tpm=10000,
        created_at=datetime.now(timezone.utc),
    )


def _cloudevent_headers() -> dict:
    return {
        "ce-specversion": "1.0",
        "ce-type": "google.cloud.storage.object.v1.finalized",
        "ce-source": f"//storage.googleapis.com/projects/_/buckets/{INPUT_BUCKET}",
        "ce-id": "integ-test-event",
        "content-type": "application/json",
    }


def _cloudevent_body(job_id: str, content: bytes) -> bytes:
    return json.dumps({
        "bucket": INPUT_BUCKET,
        "name": f"{TEST_CLIENT_ID}/{job_id}/prompts.jsonl",
        "size": str(len(content)),
        "contentType": "application/x-ndjson",
    }).encode()


@pytest.fixture
def launcher_client():
    with TestClient(launcher_main.app) as c:
        yield c


# ── tests ─────────────────────────────────────────────────────────────────────

class TestHappyPath:
    """Valid file → QUEUED in Firestore, counts correct, errors.jsonl written."""

    def test_status_becomes_queued(self, launcher_client, test_job_id):
        # Seed Firestore
        create_job(_make_job_record(test_job_id))

        # Upload real JSONL to real GCS
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _MIXED_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job") as mock_spawn:
            resp = launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _MIXED_JSONL),
                headers=_cloudevent_headers(),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "queued"

        # Fetch from REAL Firestore
        record = get_job(test_job_id)
        assert record is not None
        assert record.status == JobStatus.QUEUED

    def test_prompt_counts_written_to_firestore(self, launcher_client, test_job_id):
        create_job(_make_job_record(test_job_id))
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _MIXED_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job"):
            launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _MIXED_JSONL),
                headers=_cloudevent_headers(),
            )

        record = get_job(test_job_id)
        assert record is not None
        assert record.prompt_count == 5,  f"Expected 5 valid, got {record.prompt_count}"
        assert record.invalid_count == 2, f"Expected 2 invalid, got {record.invalid_count}"
        assert record.file_size_bytes == len(_MIXED_JSONL)

    def test_errors_jsonl_written_to_gcs(self, launcher_client, test_job_id):
        create_job(_make_job_record(test_job_id))
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _MIXED_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job"):
            launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _MIXED_JSONL),
                headers=_cloudevent_headers(),
            )

        # errors.jsonl must exist in the REAL GCS output bucket
        error_lines = list(stream_read(
            OUTPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/errors.jsonl",
        ))
        assert len(error_lines) == 2, f"Expected 2 error lines, got {len(error_lines)}"

        for line in error_lines:
            record = json.loads(line)
            assert record["stage"] == "validation"
            assert record["prompt_id"] is None
            assert "error" in record

    def test_spawner_called_with_correct_args(self, launcher_client, test_job_id):
        create_job(_make_job_record(test_job_id))
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _VALID_ONLY_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job") as mock_spawn:
            launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _VALID_ONLY_JSONL),
                headers=_cloudevent_headers(),
            )

        mock_spawn.assert_called_once()
        call_kwargs = mock_spawn.call_args.kwargs
        assert call_kwargs["prompt_count"] == 2
        assert call_kwargs["prompts_path"] == f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl"
        assert call_kwargs["job"].job_id == test_job_id

    def test_timestamps_set_in_firestore(self, launcher_client, test_job_id):
        create_job(_make_job_record(test_job_id))
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _VALID_ONLY_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job"):
            launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _VALID_ONLY_JSONL),
                headers=_cloudevent_headers(),
            )

        record = get_job(test_job_id)
        assert record.uploaded_at is not None
        assert record.queued_at is not None


class TestConcurrency:
    """Second job while first is active → PENDING in Firestore, no pod spawned."""

    def test_second_job_becomes_pending(
        self, launcher_client, test_job_id, test_job_b_id
    ):
        # Job A is already QUEUED (active)
        job_a = _make_job_record(test_job_id)
        job_a.status = JobStatus.QUEUED
        create_job(job_a)

        # Job B arrives
        job_b = _make_job_record(test_job_b_id)
        create_job(job_b)
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_b_id}/prompts.jsonl",
            _VALID_ONLY_JSONL,
        )

        with patch("launcher_main_integ.spawn_execution_job") as mock_spawn:
            resp = launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_b_id, _VALID_ONLY_JSONL),
                headers=_cloudevent_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"
        mock_spawn.assert_not_called()

        # Real Firestore check
        record_b = get_job(test_job_b_id)
        assert record_b is not None
        assert record_b.status == JobStatus.PENDING


class TestInvalidFile:
    """Fully unparseable file → FAILED in Firestore, no pod spawned."""

    def test_non_jsonl_file_sets_failed(self, launcher_client, test_job_id):
        create_job(_make_job_record(test_job_id))
        write_bytes(
            INPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl",
            _FULLY_INVALID,
        )

        with patch("launcher_main_integ.spawn_execution_job") as mock_spawn:
            resp = launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _FULLY_INVALID),
                headers=_cloudevent_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        mock_spawn.assert_not_called()

        # Real Firestore
        record = get_job(test_job_id)
        assert record is not None
        assert record.status == JobStatus.FAILED


class TestDuplicateEvent:
    """Eventarc retries the same event — launcher ignores it."""

    def test_duplicate_event_ignored(self, launcher_client, test_job_id):
        # Seed as already QUEUED (simulates a second delivery of the same event)
        job = _make_job_record(test_job_id)
        job.status = JobStatus.QUEUED
        create_job(job)

        with patch("launcher_main_integ.spawn_execution_job") as mock_spawn:
            resp = launcher_client.post(
                "/",
                content=_cloudevent_body(test_job_id, _VALID_ONLY_JSONL),
                headers=_cloudevent_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"
        mock_spawn.assert_not_called()

        # Firestore status must remain QUEUED (not overwritten)
        record = get_job(test_job_id)
        assert record.status == JobStatus.QUEUED
