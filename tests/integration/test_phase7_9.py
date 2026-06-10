"""
Integration tests for Phases 7, 8, and 9.

Phase 7 - Checkpointing:
  Real GCS used for state.json reads/writes. LiteLLM mocked.

Phase 8 - Job Queue:
  Real Firestore for job status transitions. GKE spawner mocked.

Phase 9 - Status + Results API:
  Real Firestore + real GCS. Unkey auth mocked via dependency_overrides.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services", "execution"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services", "api"))

from tests.integration.conftest import INPUT_BUCKET, OUTPUT_BUCKET, TEST_CLIENT_ID
from shared.firestore import create_job, get_job
from shared.gcs import write_bytes, stream_read
from shared.models.job import JobRecord, JobStatus
from config import JobConfig
from loops.dispatch import run as dispatch_run


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_response(content="ok", prompt_tokens=8, completion_tokens=5):
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_prompts(n):
    lines = [json.dumps({"prompt_id": i, "prompt": f"q{i}"}) for i in range(1, n + 1)]
    return "\n".join(lines).encode()


def _make_job_record(job_id, status=JobStatus.QUEUED, **kwargs):
    return JobRecord(
        job_id=job_id, client_id=TEST_CLIENT_ID, provider="openai",
        model="gpt-4o-mini", status=status, max_retries=3,
        created_at=datetime.now(timezone.utc), **kwargs,
    )


def _make_config(job_id, prompt_count=5):
    return JobConfig(
        job_id=job_id, client_id=TEST_CLIENT_ID, provider="openai",
        model="gpt-4o-mini", rpm_limit=60, tpm_limit=0, max_retries=3,
        input_bucket=INPUT_BUCKET, output_bucket=OUTPUT_BUCKET,
        prompts_path=f"{TEST_CLIENT_ID}/{job_id}/prompts.jsonl",
        prompt_count=prompt_count,
        firestore_project_id=os.environ.get("FIRESTORE_PROJECT_ID", "promptforge-1212"),
        api_key_ref=None,
    )


def _load_api_app():
    spec = importlib.util.spec_from_file_location(
        f"api_main_p9_{os.getpid()}",
        os.path.join(PROJECT_ROOT, "services", "api", "main.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Phase 7: Checkpointing ────────────────────────────────────────────────────

class TestCheckpointing:
    def test_checkpoint_written_to_gcs(self, test_job_id):
        """After dispatch, state.json must exist in real GCS with required fields."""
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl", _make_prompts(3))
        create_job(_make_job_record(test_job_id))

        with patch("loops.dispatch.litellm.completion", return_value=_fake_response()), \
             patch("services.execution.checkpoint.Checkpoint.should_save", return_value=True):
            asyncio.run(dispatch_run(_make_config(test_job_id, prompt_count=3), "test-key"))

        raw = b"".join(stream_read(OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/state.json"))
        state = json.loads(raw)
        assert "completed" in state
        assert "failed" in state
        assert "part_num" in state
        assert "rate" in state
        assert "checkpoint_at" in state

    def test_resume_skips_already_processed_prompts(self, test_job_id):
        """Pre-written checkpoint with completed=3 causes dispatch to skip first 3 prompts."""
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl", _make_prompts(5))
        create_job(_make_job_record(test_job_id))

        state = {
            "completed": 3, "failed": 0, "part_num": 0, "rate": {},
            "checkpoint_at": datetime.now(timezone.utc).isoformat(),
        }
        write_bytes(
            OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/state.json",
            json.dumps(state).encode(), content_type="application/json",
        )

        call_count = 0

        def counting_llm(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _fake_response()

        with patch("loops.dispatch.litellm.completion", side_effect=counting_llm):
            result = asyncio.run(dispatch_run(_make_config(test_job_id, prompt_count=5), "test-key"))

        assert call_count == 2           # only prompts 4 and 5 dispatched
        assert result.completed == 5     # 3 from checkpoint + 2 new
        assert result.failed == 0


# ── Phase 8: Job Queue ────────────────────────────────────────────────────────

class TestJobQueue:
    def test_maybe_start_next_job_promotes_pending_job(self, test_job_id, test_job_b_id):
        """After job A completes, job B (PENDING) is promoted to QUEUED and spawned."""
        from services.execution.queue import maybe_start_next_job

        upload_path = f"{TEST_CLIENT_ID}/{test_job_b_id}/prompts.jsonl"
        write_bytes(INPUT_BUCKET, upload_path, _make_prompts(3))
        create_job(_make_job_record(test_job_id, status=JobStatus.COMPLETED))
        create_job(_make_job_record(
            test_job_b_id, status=JobStatus.PENDING,
            upload_path=upload_path, prompt_count=3,
        ))

        os.environ["GKE_CLUSTER_ENDPOINT"] = "fake-endpoint"
        os.environ["EXECUTION_IMAGE"] = "fake-image"

        with patch("services.execution.queue.spawn_execution_job") as mock_spawn:
            started = maybe_start_next_job(_make_config(test_job_id))

        assert started is True
        mock_spawn.assert_called_once()
        # Verify correct job passed to spawner
        call_args = mock_spawn.call_args
        spawned_job = call_args[1].get("job") if call_args[1] else call_args[0][0]
        assert spawned_job.job_id == test_job_b_id

        # Firestore: job B must be QUEUED with queued_at set
        record_b = get_job(test_job_b_id)
        assert record_b.status == JobStatus.QUEUED
        assert record_b.queued_at is not None

    def test_no_pending_job_returns_false(self, test_job_id):
        """If no PENDING jobs exist for client, returns False and spawner not called."""
        from services.execution.queue import maybe_start_next_job

        create_job(_make_job_record(test_job_id, status=JobStatus.COMPLETED))
        os.environ["GKE_CLUSTER_ENDPOINT"] = "fake-endpoint"
        os.environ["EXECUTION_IMAGE"] = "fake-image"

        with patch("services.execution.queue.spawn_execution_job") as mock_spawn:
            started = maybe_start_next_job(_make_config(test_job_id))

        assert started is False
        mock_spawn.assert_not_called()


# ── Phase 9: Status + Results API ────────────────────────────────────────────

class TestStatusEndpoint:
    def test_get_status_returns_job_fields(self, test_job_id):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        create_job(_make_job_record(
            test_job_id, status=JobStatus.COMPLETED, prompt_count=5,
            completed_count=4, failed_count=1,
            started_processing_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        ))

        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: TEST_CLIENT_ID
        client = TestClient(api.app)

        resp = client.get(f"/v1/jobs/{test_job_id}/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == test_job_id
        assert body["status"] == "COMPLETED"
        assert body["prompt_count"] == 5
        assert body["completed_count"] == 4
        assert body["failed_count"] == 1
        assert body["completed_at"] is not None

    def test_get_status_wrong_client_returns_404(self, test_job_id):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        create_job(_make_job_record(test_job_id, status=JobStatus.QUEUED))
        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: "other-client"
        client = TestClient(api.app)

        resp = client.get(f"/v1/jobs/{test_job_id}/status")
        assert resp.status_code == 404

    def test_get_status_nonexistent_returns_404(self):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: TEST_CLIENT_ID
        client = TestClient(api.app)

        resp = client.get("/v1/jobs/does-not-exist/status")
        assert resp.status_code == 404


class TestResultsEndpoint:
    def test_get_results_returns_signed_urls(self, test_job_id):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        create_job(_make_job_record(test_job_id, status=JobStatus.COMPLETED))
        write_bytes(
            OUTPUT_BUCKET,
            f"{TEST_CLIENT_ID}/{test_job_id}/results_final.jsonl",
            json.dumps({"prompt_id": 1, "response": "ok"}).encode(),
        )

        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: TEST_CLIENT_ID
        client = TestClient(api.app)

        resp = client.get(f"/v1/jobs/{test_job_id}/results")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == test_job_id
        assert body["status"] == "COMPLETED"
        assert len(body["files"]) >= 1
        f = body["files"][0]
        assert f["url"].startswith("https://storage.googleapis.com/")
        assert "results_" in f["filename"]
        assert "expires_at" in f

    def test_get_results_non_completed_returns_409(self, test_job_id):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        create_job(_make_job_record(test_job_id, status=JobStatus.PROCESSING))
        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: TEST_CLIENT_ID
        client = TestClient(api.app)

        resp = client.get(f"/v1/jobs/{test_job_id}/results")
        assert resp.status_code == 409

    def test_get_results_wrong_client_returns_404(self, test_job_id):
        from fastapi.testclient import TestClient
        from middleware.auth import verify_api_key

        create_job(_make_job_record(test_job_id, status=JobStatus.COMPLETED))
        api = _load_api_app()
        api.app.dependency_overrides[verify_api_key] = lambda: "another-client"
        client = TestClient(api.app)

        resp = client.get(f"/v1/jobs/{test_job_id}/results")
        assert resp.status_code == 404
