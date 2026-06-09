"""
Integration-style tests for services/launcher/main.py.

Sends real HTTP POST requests (CloudEvent binary format) to the FastAPI app
and mocks every external dependency (Firestore, validator, spawner).
No real GCS, Firestore, or Kubernetes calls are made.
"""

import sys
import os
import importlib.util

# Project root on sys.path so shared/ is importable.
# Launcher dir also added so validator / spawner are importable inside main.py.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/launcher"))

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Env vars must be set before importing the app module.
os.environ.setdefault("FIRESTORE_PROJECT_ID", "test-project")
os.environ.setdefault("GCS_INPUT_BUCKET", "test-input-bucket")
os.environ.setdefault("GCS_OUTPUT_BUCKET", "test-output-bucket")
os.environ.setdefault("SENTRY_DSN", "")

# Load services/launcher/main.py under the unique module name "launcher_main"
# so it never collides with services/api/main.py, which pytest may have already
# imported and cached under the bare name "main".
_LAUNCHER_MAIN_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../services/launcher/main.py")
)
_spec = importlib.util.spec_from_file_location("launcher_main", _LAUNCHER_MAIN_PATH)
assert _spec is not None and _spec.loader is not None, (
    f"Could not load launcher main from {_LAUNCHER_MAIN_PATH}"
)
launcher_main = importlib.util.module_from_spec(_spec)
sys.modules["launcher_main"] = launcher_main
_spec.loader.exec_module(launcher_main)  # type: ignore[union-attr]

app = launcher_main.app

from shared.models.job import JobRecord, JobStatus
from validator import ValidationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CLIENT_ID = "client_abc123"
JOB_ID = "job-0000-1111-2222-3333"
OBJECT_NAME = f"{CLIENT_ID}/{JOB_ID}/prompts.jsonl"
FILE_SIZE = 2048


def _cloudevent_headers(bucket: str = "test-input-bucket") -> dict:
    """Minimal CloudEvent binary-mode headers for an OBJECT_FINALIZE event."""
    return {
        "ce-specversion": "1.0",
        "ce-type": "google.cloud.storage.object.v1.finalized",
        "ce-source": f"//storage.googleapis.com/projects/_/buckets/{bucket}",
        "ce-id": "test-event-id-001",
        "ce-time": "2026-06-09T10:00:00Z",
        "content-type": "application/json",
    }


def _gcs_event_body(name: str = OBJECT_NAME, size: int = FILE_SIZE) -> bytes:
    """JSON body that Eventarc delivers for an OBJECT_FINALIZE event."""
    return json.dumps({
        "bucket": "test-input-bucket",
        "name": name,
        "size": str(size),
        "contentType": "application/x-ndjson",
        "kind": "storage#object",
    }).encode()


def _make_job(status: JobStatus = JobStatus.AWAITING_UPLOAD) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        client_id=CLIENT_ID,
        provider="openai",
        model="gpt-4o",
        status=status,
        max_retries=3,
        rpm=100,
        tpm=50000,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Happy path: valid file, no active job → status=QUEUED, pod spawned
# ---------------------------------------------------------------------------

def test_happy_path_queued(client):
    job = _make_job(JobStatus.AWAITING_UPLOAD)
    validation_result = ValidationResult(prompt_count=10, invalid_count=0)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.has_active_job", return_value=False),
        patch("launcher_main.update_job") as mock_update,
        patch("launcher_main.validate_prompts", return_value=validation_result),
        patch("launcher_main.spawn_execution_job") as mock_spawn,
    ):
        resp = client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    mock_spawn.assert_called_once()
    update_calls = mock_update.call_args_list
    assert any(
        c.kwargs.get("status") == JobStatus.QUEUED
        for c in update_calls
    ), "update_job must be called with status=QUEUED"


def test_spawn_receives_correct_args(client):
    job = _make_job(JobStatus.AWAITING_UPLOAD)
    validation_result = ValidationResult(prompt_count=42, invalid_count=2)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.has_active_job", return_value=False),
        patch("launcher_main.update_job"),
        patch("launcher_main.validate_prompts", return_value=validation_result),
        patch("launcher_main.spawn_execution_job") as mock_spawn,
    ):
        client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    mock_spawn.assert_called_once_with(
        job=job,
        prompt_count=42,
        prompts_path=OBJECT_NAME,
    )


def test_update_job_sets_counts(client):
    job = _make_job(JobStatus.AWAITING_UPLOAD)
    validation_result = ValidationResult(prompt_count=7, invalid_count=3)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.has_active_job", return_value=False),
        patch("launcher_main.update_job") as mock_update,
        patch("launcher_main.validate_prompts", return_value=validation_result),
        patch("launcher_main.spawn_execution_job"),
    ):
        client.post("/", content=_gcs_event_body(size=999), headers=_cloudevent_headers())

    queued_call = mock_update.call_args
    assert queued_call.kwargs["prompt_count"] == 7
    assert queued_call.kwargs["invalid_count"] == 3
    assert queued_call.kwargs["file_size_bytes"] == 999


# ---------------------------------------------------------------------------
# Client already has active job → status=PENDING, no pod spawned
# ---------------------------------------------------------------------------

def test_second_job_becomes_pending(client):
    job = _make_job(JobStatus.AWAITING_UPLOAD)
    validation_result = ValidationResult(prompt_count=5, invalid_count=0)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.has_active_job", return_value=True),
        patch("launcher_main.update_job") as mock_update,
        patch("launcher_main.validate_prompts", return_value=validation_result),
        patch("launcher_main.spawn_execution_job") as mock_spawn,
    ):
        resp = client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    mock_spawn.assert_not_called()
    assert mock_update.call_args.kwargs["status"] == JobStatus.PENDING


# ---------------------------------------------------------------------------
# Completely invalid file → status=FAILED, no pod spawned
# ---------------------------------------------------------------------------

def test_invalid_file_sets_failed(client):
    from validator import InvalidFileError

    job = _make_job(JobStatus.AWAITING_UPLOAD)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.has_active_job", return_value=False),
        patch("launcher_main.update_job") as mock_update,
        patch("launcher_main.validate_prompts", side_effect=InvalidFileError("not JSONL")),
        patch("launcher_main.spawn_execution_job") as mock_spawn,
    ):
        resp = client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    mock_spawn.assert_not_called()
    mock_update.assert_called_once_with(JOB_ID, status=JobStatus.FAILED)


# ---------------------------------------------------------------------------
# Duplicate Eventarc delivery (job already past AWAITING_UPLOAD)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.COMPLETED])
def test_duplicate_event_is_ignored(client, status):
    job = _make_job(status)

    with (
        patch("launcher_main.get_job", return_value=job),
        patch("launcher_main.validate_prompts") as mock_validate,
        patch("launcher_main.spawn_execution_job") as mock_spawn,
        patch("launcher_main.update_job") as mock_update,
    ):
        resp = client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"
    mock_validate.assert_not_called()
    mock_spawn.assert_not_called()
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# Job not in Firestore
# ---------------------------------------------------------------------------

def test_unknown_job_id_returns_not_found(client):
    with (
        patch("launcher_main.get_job", return_value=None),
        patch("launcher_main.validate_prompts") as mock_validate,
        patch("launcher_main.spawn_execution_job") as mock_spawn,
    ):
        resp = client.post("/", content=_gcs_event_body(), headers=_cloudevent_headers())

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"
    mock_validate.assert_not_called()
    mock_spawn.assert_not_called()


# ---------------------------------------------------------------------------
# Unexpected object paths are silently ignored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("object_name", [
    "client_abc/job_123/other_file.jsonl",    # wrong filename
    "client_abc/job_123/prompts.jsonl/extra", # too many segments
    "just-a-file.jsonl",                      # flat path
    "",                                       # empty
])
def test_unexpected_object_path_ignored(client, object_name):
    with (
        patch("launcher_main.get_job") as mock_get,
        patch("launcher_main.validate_prompts") as mock_validate,
    ):
        resp = client.post(
            "/",
            content=_gcs_event_body(name=object_name),
            headers=_cloudevent_headers(),
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    mock_get.assert_not_called()
    mock_validate.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed CloudEvent (missing required ce-* headers) → 400
# ---------------------------------------------------------------------------

def test_malformed_cloudevent_returns_400(client):
    resp = client.post(
        "/",
        content=b"not json at all",
        headers={"content-type": "application/json"},  # no ce-specversion etc.
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
