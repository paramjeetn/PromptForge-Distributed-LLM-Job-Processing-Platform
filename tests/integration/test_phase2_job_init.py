"""
Phase 2 integration tests — POST /v1/jobs/init

Tests the full API flow against real GCP:
  - Firestore: job document actually created
  - GCS: signed URL actually works for file upload

Mocked:
  - verify_api_key (Unkey) — overridden via FastAPI dependency_overrides
    to return a fixed test client_id. Unkey is a third-party SaaS; its
    integration is unit-tested separately.
"""

import importlib.util
import json
import sys
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

# conftest.py already sets sys.path and loads .env
from tests.integration.conftest import INPUT_BUCKET, TEST_CLIENT_ID

from shared.firestore import get_job
from shared.gcs import stream_read, delete_blob
from shared.models.job import JobStatus

# ── load api/main.py under a unique module name ───────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "api_main_integ",
    str(PROJECT_ROOT / "services" / "api" / "main.py"),
)
assert _spec and _spec.loader
api_main = importlib.util.module_from_spec(_spec)
sys.modules["api_main_integ"] = api_main
_spec.loader.exec_module(api_main)  # type: ignore[union-attr]

# Import verify_api_key from the api service (path is on sys.path via conftest)
from middleware.auth import verify_api_key  # type: ignore[import]


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    """TestClient with Unkey auth bypassed — returns fixed test client_id."""
    api_main.app.dependency_overrides[verify_api_key] = lambda: TEST_CLIENT_ID
    with TestClient(api_main.app) as c:
        yield c
    api_main.app.dependency_overrides.clear()


# ── helpers ───────────────────────────────────────────────────────────────────

def _init_payload(model: str = "gpt-4o") -> dict:
    return {"provider": "openai", "model": model, "rpm": 60, "tpm": 10000, "max_retries": 3}


# ── tests ─────────────────────────────────────────────────────────────────────

class TestJobInitFirestore:
    """POST /v1/jobs/init → Firestore doc is created with correct fields."""

    def test_creates_firestore_doc(self, api_client, test_job_id):
        resp = api_client.post(
            "/v1/jobs/init",
            json=_init_payload(),
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "job_id" in body
        assert "upload_url" in body
        assert "expires_at" in body

        # Fetch the REAL Firestore document
        job_id = body["job_id"]
        record = get_job(job_id)

        assert record is not None, f"No Firestore doc for job_id={job_id}"
        assert record.status == JobStatus.AWAITING_UPLOAD
        assert record.client_id == TEST_CLIENT_ID
        assert record.provider == "openai"
        assert record.model == "gpt-4o"
        assert record.rpm == 60
        assert record.tpm == 10000
        assert record.max_retries == 3
        assert record.created_at is not None

        # Register this job_id for cleanup (override the fixture's job_id)
        # The autouse cleanup fixture uses test_job_id, so register this one manually.
        from shared.firestore import _get_client
        try:
            _get_client().collection("jobs").document(job_id).delete()
        except Exception:
            pass

    def test_response_shape_is_correct(self, api_client, test_job_id):
        resp = api_client.post(
            "/v1/jobs/init",
            json=_init_payload(),
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert isinstance(body["job_id"], str) and len(body["job_id"]) > 0
        assert body["upload_url"].startswith("https://storage.googleapis.com/")
        assert "expires_at" in body

        from shared.firestore import _get_client
        try:
            _get_client().collection("jobs").document(body["job_id"]).delete()
        except Exception:
            pass


class TestJobInitSignedUrl:
    """The signed URL returned by /v1/jobs/init can actually upload a file to GCS."""

    def test_signed_url_accepts_file_upload(self, api_client, test_job_id):
        resp = api_client.post(
            "/v1/jobs/init",
            json=_init_payload(),
            headers={"X-API-Key": "test-key"},
        )
        assert resp.status_code == 202
        body = resp.json()
        upload_url = body["upload_url"]
        job_id = body["job_id"]

        # Build a small test JSONL payload
        prompts = [
            {"prompt_id": 1, "prompt": "Integration test prompt one"},
            {"prompt_id": 2, "prompt": "Integration test prompt two"},
            {"prompt_id": 3, "prompt": "Integration test prompt three"},
        ]
        content = "\n".join(json.dumps(p) for p in prompts).encode()

        # PUT to the REAL signed GCS URL
        put_resp = httpx.put(
            upload_url,
            content=content,
            headers={"Content-Type": "application/x-ndjson"},
        )
        assert put_resp.status_code == 200, (
            f"GCS signed URL upload failed: {put_resp.status_code} {put_resp.text}"
        )

        # Confirm the file actually landed in GCS by streaming it back
        from shared.firestore import get_job as _get_job
        record = _get_job(job_id)
        assert record is not None
        upload_path = record.upload_path  # "{client_id}/{job_id}/prompts.jsonl"

        lines = list(stream_read(INPUT_BUCKET, upload_path))
        assert len(lines) == 3, f"Expected 3 lines in GCS, got {len(lines)}"

        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["prompt_id"] == 1
        assert parsed[2]["prompt"] == "Integration test prompt three"

        # cleanup
        from shared.firestore import _get_client
        try:
            delete_blob(INPUT_BUCKET, upload_path)
        except Exception:
            pass
        try:
            _get_client().collection("jobs").document(job_id).delete()
        except Exception:
            pass
