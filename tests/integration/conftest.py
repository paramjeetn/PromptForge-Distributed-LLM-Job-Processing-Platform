"""
Shared fixtures for integration tests.

All integration tests run against real GCP resources:
  - Firestore: project promptforge-1212, (default) database
  - GCS:       promptforge-input-promptforge-1212  /  promptforge-output-promptforge-1212

Prerequisites:
  - gcloud auth application-default login
  - .env file present at project root with GCP values filled in

Each test gets a unique job_id prefixed with "integ-" so test data is easy to
identify and clean up. Cleanup runs automatically after every test via the
`cleanup` autouse fixture — even on failure.
"""

import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

# ── path setup ────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "api"))
sys.path.insert(0, str(PROJECT_ROOT / "services" / "launcher"))

# Load real env vars from .env file at project root
load_dotenv(PROJECT_ROOT / ".env")

# ── real GCP resource names ───────────────────────────────────────────────────
INPUT_BUCKET  = os.environ.get("GCS_INPUT_BUCKET",  "promptforge-input-promptforge-1212")
OUTPUT_BUCKET = os.environ.get("GCS_OUTPUT_BUCKET", "promptforge-output-promptforge-1212")
FIRESTORE_PROJECT = os.environ.get("FIRESTORE_PROJECT_ID", "promptforge-1212")

# Ensure env vars are set for all modules that read them
os.environ["GCS_INPUT_BUCKET"]     = INPUT_BUCKET
os.environ["GCS_OUTPUT_BUCKET"]    = OUTPUT_BUCKET
os.environ["FIRESTORE_PROJECT_ID"] = FIRESTORE_PROJECT
os.environ.setdefault("SENTRY_DSN", "")

# ── test identity ─────────────────────────────────────────────────────────────
TEST_CLIENT_ID = "integ-test-client"


@pytest.fixture
def test_client_id() -> str:
    return TEST_CLIENT_ID


@pytest.fixture
def test_job_id() -> str:
    """Unique job ID for each test run."""
    return f"integ-{uuid.uuid4().hex[:8]}"


# ── autouse cleanup ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup(test_job_id: str):
    """
    Delete all GCP artefacts created by the test, even if the test fails.
    Swallows all errors so a cleanup failure never hides the test failure.
    """
    yield  # test runs here

    from shared.gcs import delete_blob
    from shared.firestore import _get_client  # internal — only for cleanup

    _blobs_to_delete = [
        (INPUT_BUCKET,  f"{TEST_CLIENT_ID}/{test_job_id}/prompts.jsonl"),
        (OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/errors.jsonl"),
        (OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{test_job_id}/state.json"),
    ]

    for bucket, path in _blobs_to_delete:
        try:
            delete_blob(bucket, path)
        except Exception:
            pass

    try:
        _get_client().collection("jobs").document(test_job_id).delete()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def cleanup_job_b(request):
    """
    Extra cleanup for tests that create a second job (job_b).
    Only active when the test uses the `test_job_b_id` fixture.
    """
    # Capture value before yielding — fixture may be torn down by the time
    # our teardown runs, making getfixturevalue unavailable.
    job_b_id = (
        request.getfixturevalue("test_job_b_id")
        if "test_job_b_id" in request.fixturenames
        else None
    )
    yield

    if not job_b_id:
        return

    from shared.gcs import delete_blob
    from shared.firestore import _get_client

    for bucket, path in [
        (INPUT_BUCKET,  f"{TEST_CLIENT_ID}/{job_b_id}/prompts.jsonl"),
        (OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{job_b_id}/errors.jsonl"),
    ]:
        try:
            delete_blob(bucket, path)
        except Exception:
            pass

    try:
        _get_client().collection("jobs").document(job_b_id).delete()
    except Exception:
        pass


@pytest.fixture
def test_job_b_id() -> str:
    return f"integ-{uuid.uuid4().hex[:8]}"
