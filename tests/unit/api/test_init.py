import sys
import os

# Allow imports from project root (shared/) and services/api/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/api"))

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("FIRESTORE_PROJECT_ID", "test-project")
os.environ.setdefault("GCS_INPUT_BUCKET", "test-input-bucket")
os.environ.setdefault("GCS_OUTPUT_BUCKET", "test-output-bucket")
os.environ.setdefault("UNKEY_ROOT_KEY", "test-root-key")
os.environ.setdefault("UNKEY_API_ID", "test-api-id")
os.environ.setdefault("SENTRY_DSN", "")

from main import app
from middleware.auth import verify_api_key

MOCK_CLIENT_ID = "client_abc123"
MOCK_UPLOAD_URL = "https://storage.googleapis.com/signed-url"
MOCK_EXPIRES_AT = datetime(2026, 6, 10, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def client():
    # Override the auth dependency so tests never call Unkey
    app.dependency_overrides[verify_api_key] = lambda: MOCK_CLIENT_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_dependencies():
    with (
        patch("routes.jobs.generate_signed_upload_url", return_value=(MOCK_UPLOAD_URL, MOCK_EXPIRES_AT)),
        patch("routes.jobs.create_job"),
    ):
        yield


def test_job_init_returns_202(client):
    response = client.post(
        "/v1/jobs/init",
        headers={"X-API-Key": "test-key"},
        json={"provider": "openai", "model": "gpt-4o"},
    )
    assert response.status_code == 202


def test_job_init_response_shape(client):
    response = client.post(
        "/v1/jobs/init",
        headers={"X-API-Key": "test-key"},
        json={"provider": "openai", "model": "gpt-4o"},
    )
    data = response.json()
    assert "job_id" in data
    assert "upload_url" in data
    assert "expires_at" in data
    assert data["upload_url"] == MOCK_UPLOAD_URL


def test_job_init_invalid_provider(client):
    response = client.post(
        "/v1/jobs/init",
        headers={"X-API-Key": "test-key"},
        json={"provider": "cohere", "model": "command"},
    )
    assert response.status_code == 422


def test_job_init_missing_provider(client):
    response = client.post(
        "/v1/jobs/init",
        headers={"X-API-Key": "test-key"},
        json={"model": "gpt-4o"},
    )
    assert response.status_code == 422


def test_job_init_optional_fields(client):
    response = client.post(
        "/v1/jobs/init",
        headers={"X-API-Key": "test-key"},
        json={"provider": "anthropic", "model": "claude-opus-4-6", "rpm": 100, "tpm": 50000, "max_retries": 5},
    )
    assert response.status_code == 202
