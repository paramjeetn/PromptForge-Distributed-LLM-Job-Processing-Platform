"""
Phase 4 integration tests — Execution Engine

Tests the full dispatch loop against real GCP resources:
  - GCS: prompts.jsonl uploaded to real input bucket before each test
  - GCS: results_*.jsonl and errors.jsonl read from real output bucket after
  - Firestore: job status transitions verified via real Firestore

Mocked:
  - litellm.acompletion: returns deterministic fake responses.
    No real LLM provider is called — no API key needed.

Run:
    pytest tests/integration/test_phase4_execution.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# ── path + env setup (must come before any service imports) ───────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "services", "execution"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

os.environ.setdefault("SENTRY_DSN", "")

from tests.integration.conftest import INPUT_BUCKET, OUTPUT_BUCKET, TEST_CLIENT_ID

from shared.firestore import create_job, get_job
from shared.gcs import write_bytes, stream_read, delete_blob
from shared.models.job import JobRecord, JobStatus

from config import JobConfig
from loops.dispatch import run as dispatch_run


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_response(content: str = "Short answer.", prompt_tokens: int = 8, completion_tokens: int = 6):
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_prompts(n: int) -> bytes:
    lines = [json.dumps({"prompt_id": i, "prompt": f"Short answer: what is {i}+1?"}) for i in range(1, n + 1)]
    return "\n".join(lines).encode()


def _make_job_record(job_id: str, status: JobStatus = JobStatus.QUEUED) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        client_id=TEST_CLIENT_ID,
        provider="openai",
        model="gpt-4o",
        status=status,
        max_retries=3,
        rpm=60,
        tpm=0,
        created_at=datetime.now(timezone.utc),
    )


def _make_config(job_id: str, prompt_count: int = 5, **overrides) -> JobConfig:
    defaults = dict(
        job_id=job_id,
        client_id=TEST_CLIENT_ID,
        provider="openai",
        model="gpt-4o",
        rpm_limit=60,
        tpm_limit=0,
        max_retries=3,
        input_bucket=INPUT_BUCKET,
        output_bucket=OUTPUT_BUCKET,
        prompts_path=f"{TEST_CLIENT_ID}/{job_id}/prompts.jsonl",
        prompt_count=prompt_count,
        firestore_project_id=os.environ.get("FIRESTORE_PROJECT_ID", "promptforge-1212"),
        api_key_ref=None,
    )
    defaults.update(overrides)
    return JobConfig(**defaults)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def exec_job_id(test_job_id: str) -> str:
    """Re-use the conftest unique job ID."""
    return test_job_id


# ── tests ──────────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_results_written_to_gcs(self, exec_job_id: str):
        """5 prompts → 5 result records in GCS output bucket."""
        prompts = _make_prompts(5)
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/prompts.jsonl", prompts)
        create_job(_make_job_record(exec_job_id))

        with patch("loops.dispatch.litellm.acompletion", new_callable=AsyncMock,
                   return_value=_fake_response("2")):
            result = asyncio.run(dispatch_run(_make_config(exec_job_id, prompt_count=5), "test-key"))

        assert result.completed == 5
        assert result.failed == 0

        # Read results from real GCS
        result_lines = list(stream_read(OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/results_final.jsonl"))
        assert len(result_lines) == 5
        parsed = [json.loads(l) for l in result_lines]
        prompt_ids = {r["prompt_id"] for r in parsed}
        assert prompt_ids == {1, 2, 3, 4, 5}
        for r in parsed:
            assert "response" in r
            assert "tokens" in r
            assert "latency_ms" in r

    def test_firestore_updated_on_completion(self, exec_job_id: str):
        """Dispatch run itself doesn't update Firestore — that's main.py's job.
        But we can verify the job record is still QUEUED (dispatch doesn't touch it)
        and the results are in GCS."""
        prompts = _make_prompts(3)
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/prompts.jsonl", prompts)
        create_job(_make_job_record(exec_job_id))

        with patch("loops.dispatch.litellm.acompletion", new_callable=AsyncMock,
                   return_value=_fake_response("short")):
            result = asyncio.run(dispatch_run(_make_config(exec_job_id, prompt_count=3), "test-key"))

        assert result.completed == 3

        # Firestore record should still exist (dispatch doesn't update it)
        record = get_job(exec_job_id)
        assert record is not None
        assert record.status == JobStatus.QUEUED  # unchanged by dispatch_run


class TestErrorHandling:
    def test_non_retryable_error_goes_to_errors_jsonl(self, exec_job_id: str):
        """A 400-style error → error record written to GCS errors.jsonl."""
        import litellm as _litellm
        prompts = _make_prompts(1)
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/prompts.jsonl", prompts)
        create_job(_make_job_record(exec_job_id))

        async def bad_request(*args, **kwargs):
            raise _litellm.BadRequestError(
                message="400 invalid request", llm_provider="openai", model="gpt-4o"
            )

        with patch("loops.dispatch.litellm.acompletion", side_effect=bad_request):
            result = asyncio.run(dispatch_run(_make_config(exec_job_id, prompt_count=1), "test-key"))

        assert result.failed == 1
        assert result.completed == 0

        # Error record must be in GCS
        error_lines = list(stream_read(OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/errors.jsonl"))
        assert len(error_lines) == 1
        err = json.loads(error_lines[0])
        assert err["stage"] == "execution"
        assert err["prompt_id"] == 1

    def test_retries_eventually_succeed(self, exec_job_id: str):
        """First call raises ServiceUnavailableError, second succeeds."""
        import litellm as _litellm
        prompts = _make_prompts(1)
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/prompts.jsonl", prompts)
        create_job(_make_job_record(exec_job_id))
        call_count = 0

        async def flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _litellm.ServiceUnavailableError(
                    message="503", llm_provider="openai", model="gpt-4o"
                )
            return _fake_response("ok on retry")

        with patch("loops.dispatch.litellm.acompletion", side_effect=flaky):
            result = asyncio.run(dispatch_run(_make_config(exec_job_id, prompt_count=1), "test-key"))

        assert result.completed == 1
        assert result.failed == 0
        assert call_count == 2

        result_lines = list(stream_read(OUTPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/results_final.jsonl"))
        assert len(result_lines) == 1
        r = json.loads(result_lines[0])
        assert r["attempt"] == 2


class TestMixedResults:
    def test_partial_failures_dont_stop_job(self, exec_job_id: str):
        """Prompt 2 fails permanently; prompts 1, 3 succeed."""
        import litellm as _litellm
        prompts = _make_prompts(3)
        write_bytes(INPUT_BUCKET, f"{TEST_CLIENT_ID}/{exec_job_id}/prompts.jsonl", prompts)
        create_job(_make_job_record(exec_job_id))

        async def selective_fail(*args, **kwargs):
            prompt_text = kwargs.get("messages", [{}])[0].get("content", "")
            if "2+1" in prompt_text:  # prompt_id=2
                raise _litellm.BadRequestError(
                    message="bad", llm_provider="openai", model="gpt-4o"
                )
            return _fake_response("ok")

        with patch("loops.dispatch.litellm.acompletion", side_effect=selective_fail):
            result = asyncio.run(dispatch_run(_make_config(exec_job_id, prompt_count=3), "test-key"))

        assert result.completed == 2
        assert result.failed == 1
