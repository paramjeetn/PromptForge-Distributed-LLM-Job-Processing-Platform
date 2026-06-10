"""
Unit tests for the execution dispatch loop (Phases 4+5+6).
All external I/O (litellm, GCS) is mocked.
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../services/execution"))

os.environ.setdefault("FIRESTORE_PROJECT_ID", "test-project")
os.environ.setdefault("GCS_INPUT_BUCKET", "test-input-bucket")
os.environ.setdefault("GCS_OUTPUT_BUCKET", "test-output-bucket")

from config import JobConfig
from loops.dispatch import run, _error_record, _PromptItem


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_config(**overrides) -> JobConfig:
    defaults = dict(
        job_id="test-job-123",
        client_id="test-client",
        provider="openai",
        model="gpt-4o",
        rpm_limit=60,
        tpm_limit=0,
        max_retries=3,
        input_bucket="test-input",
        output_bucket="test-output",
        prompts_path="test-client/test-job-123/prompts.jsonl",
        prompt_count=2,
        firestore_project_id="test-project",
        api_key_ref=None,
    )
    defaults.update(overrides)
    return JobConfig(**defaults)


def _make_prompts(*texts) -> list[bytes]:
    """Return a list of JSONL bytes for use as stream_read return value."""
    lines = []
    for i, text in enumerate(texts, start=1):
        lines.append(json.dumps({"prompt_id": i, "prompt": text}).encode())
    return lines


def _fake_llm_response(content: str = "4", prompt_tokens: int = 10, completion_tokens: int = 5):
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_successful_dispatch_returns_completed_count(self):
        prompts = _make_prompts("What is 2+2?", "What is 10-3?")

        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", new_callable=AsyncMock,
                   return_value=_fake_llm_response("4")), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=2), "test-key"))

        assert result.completed == 2
        assert result.failed == 0

    def test_result_record_has_correct_shape(self):
        prompts = _make_prompts("What is 2+2?")
        captured = []

        def fake_add_result(record):
            captured.append(record)
            return False  # don't flush

        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", new_callable=AsyncMock,
                   return_value=_fake_llm_response("4")), \
             patch("loops.dispatch.ResultBuffer.add_result", side_effect=fake_add_result), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            asyncio.run(run(_make_config(prompt_count=1), "test-key"))

        assert len(captured) == 1
        r = captured[0]
        assert r["prompt_id"] == 1
        assert r["response"] == "4"
        assert "tokens" in r
        assert "latency_ms" in r
        assert r["attempt"] == 1

    def test_empty_lines_skipped(self):
        # Mix of empty and valid lines
        lines = [b"", b"   ", json.dumps({"prompt_id": 1, "prompt": "hi"}).encode()]

        with patch("loops.dispatch.stream_read", return_value=iter(lines)), \
             patch("loops.dispatch.litellm.acompletion", new_callable=AsyncMock,
                   return_value=_fake_llm_response("hi")), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=1), "test-key"))

        assert result.completed == 1


class TestRetries:
    def test_retryable_error_is_retried(self):
        import litellm as _litellm
        prompts = _make_prompts("Retry me")
        call_count = 0

        async def flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _litellm.ServiceUnavailableError(
                    message="503", llm_provider="openai", model="gpt-4o"
                )
            return _fake_llm_response("ok")

        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", side_effect=flaky), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=1, max_retries=3), "test-key"))

        assert result.completed == 1
        assert result.failed == 0
        assert call_count == 2

    def test_max_retries_exceeded_goes_to_errors(self):
        import litellm as _litellm
        prompts = _make_prompts("Always fail")

        async def always_503(*args, **kwargs):
            raise _litellm.ServiceUnavailableError(
                message="503", llm_provider="openai", model="gpt-4o"
            )

        captured_errors = []

        def fake_add_error(record):
            captured_errors.append(record)

        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", side_effect=always_503), \
             patch("loops.dispatch.ResultBuffer.add_error", side_effect=fake_add_error), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=1, max_retries=3), "test-key"))

        assert result.failed == 1
        assert result.completed == 0
        assert len(captured_errors) == 1

    def test_non_retryable_error_goes_directly_to_errors(self):
        import litellm as _litellm
        prompts = _make_prompts("Bad request")
        call_count = 0

        async def bad_request(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise _litellm.BadRequestError(
                message="400", llm_provider="openai", model="gpt-4o"
            )

        captured_errors = []

        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", side_effect=bad_request), \
             patch("loops.dispatch.ResultBuffer.add_error", side_effect=lambda r: captured_errors.append(r)), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=1, max_retries=3), "test-key"))

        # Non-retryable: called exactly once, straight to errors
        assert call_count == 1
        assert result.failed == 1
        assert len(captured_errors) == 1


class TestErrorRecordShape:
    def test_error_record_has_required_fields(self):
        item = _PromptItem(prompt_id=42, prompt="test", attempt=2)
        record = _error_record(item, "some_error")
        assert record["prompt_id"] == 42
        assert record["stage"] == "execution"
        assert record["error"] == "some_error"
        assert record["attempts"] == 3
        assert "timestamp" in record


class TestRateControllerIntegration:
    def test_429_triggers_backoff_and_retry(self):
        import litellm as _litellm
        prompts = _make_prompts("Rate limited")
        call_count = 0

        async def rate_limited(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _litellm.RateLimitError(
                    message="429", llm_provider="openai", model="gpt-4o"
                )
            return _fake_llm_response("ok")

        # Patch cooldown_remaining to 0 so test doesn't actually sleep 60s
        with patch("loops.dispatch.stream_read", return_value=iter(prompts)), \
             patch("loops.dispatch.litellm.acompletion", side_effect=rate_limited), \
             patch("loops.dispatch.RateController.cooldown_remaining", new_callable=lambda: property(lambda self: 0.0)), \
             patch("loops.dispatch.ResultBuffer.final_flush"), \
             patch("loops.dispatch.ResultBuffer.flush_results"):

            result = asyncio.run(run(_make_config(prompt_count=1, max_retries=3), "test-key"))

        assert result.completed == 1
        assert call_count == 2
